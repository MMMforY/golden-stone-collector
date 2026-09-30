"""Portable Markdown storage, extracted from the validated Golden Stone collector."""
from __future__ import annotations
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from PIL import Image

LOGGER = logging.getLogger("golden_stone_collector")
CAPTURE_ROOT_REL = Path("00-inbox") / "待整理资料"
SOURCE_APP_TYPES = {"chatgpt.exe": "ChatGPT", "codex.exe": "Codex",
                    "com.openai.chat": "ChatGPT", "com.openai.codex": "Codex"}

def ensure_target_vault(root: Path) -> None:
    if not root.is_dir():
        raise ValueError("请选择存在的 Vault 或 Markdown 文件夹。")

@dataclass(frozen=True)
class StorageOptions:
    capture_directory: str = "00-inbox/待整理资料"
    group_by_month: bool = True
    timestamp_prefix: bool = True
    template_body: str = ""

    def capture_root(self, root: Path) -> Path:
        if not isinstance(self.capture_directory, str) or any(c in self.capture_directory for c in ':\\\x00'):
            raise ValueError("收集目录请使用 / 分隔的库内相对路径。")
        rel = Path(self.capture_directory)
        if rel.is_absolute() or not self.capture_directory.strip() or ".." in rel.parts:
            raise ValueError("收集目录必须是资料库内的相对路径。")
        result = (root / rel).resolve()
        if not result.is_relative_to(root.resolve()) or result == root.resolve():
            raise ValueError("收集目录必须位于资料库内部。")
        return result

DEFAULT_BODY = """### 资料说明

<!-- 用一两句话说明这组资料来自哪里、大致是什么。 -->

### 为什么留下

<!-- 写下当时真正觉得有价值、意外或值得返回的原因。 -->
"""

def render_template(body: str, timestamp: str, card_id: str) -> str:
    return ('---\n状态: 收集中\n来源类型: 待判断\n上游来源: []\n'
            f'创建时间: "{timestamp}"\n下次回看:\n---\n\n'
            + (body or DEFAULT_BODY).strip()
            + '\n\n---\n\n**收集内容**\n\n'
            + '<!-- gs-card ' + json.dumps({"v": 1, "card_id": card_id, "origin": "collector"},
                                          ensure_ascii=False, separators=(",", ":")) + ' -->\n')

class ClipboardBusy(RuntimeError):
    pass


@dataclass
class ClipboardPayload:
    kind: str
    value: Any
    raw_html: str | None = None
    source_url: str = ""
    raw_format: str = "unknown"


@dataclass
class CapturedBlock:
    start_offset: int
    kind: str
    attachment_paths: list[Path]
    expected_suffix: bytes
    event_id: str
    expected_card_hash: str = ""


@dataclass
class PersonalNoteBlock:
    start_offset: int
    expected_suffix: bytes
    body: str
    relates_to: str
    metadata: dict[str, Any] | None




def utc_timestamp(value: datetime | None = None) -> str:
    local = value or datetime.now().astimezone()
    if local.tzinfo is None:
        local = local.astimezone()
    return local.astimezone().isoformat(timespec="seconds")


def compact_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)



def safe_name(value: str, limit: int = 80) -> str:
    value = re.sub(r'[<>:"/\\|?*\[\]#^\x00-\x1f]', " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    value = (value or "未命名")[:limit].rstrip(" .")
    if re.fullmatch(r"(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?", value, re.IGNORECASE):
        value = "_" + value
    return value


def yaml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def frontmatter_value(path: Path, field: str) -> str:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---\n", 4)
    if end < 0:
        return ""
    match = re.search(rf"^{re.escape(field)}:\s*(.*)$", text[4:end], flags=re.MULTILINE)
    if not match:
        return ""
    raw = match.group(1).strip()
    if raw in {"", '""', "''"}:
        return ""
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        value = raw.strip("\"'")
    if isinstance(value, list):
        return str(value[0]).strip() if value else ""
    return str(value).strip()


def frontmatter_has_field(path: Path, field: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return False
    end = text.find("\n---\n", 4)
    if end < 0:
        return False
    return bool(re.search(rf"^{re.escape(field)}:\s*", text[4:end], flags=re.MULTILINE))


def infer_source_type(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower().split(":", 1)[0]
    if host == "mp.weixin.qq.com":
        return "微信公众号"
    if host in {"b23.tv", "www.bilibili.com", "bilibili.com", "m.bilibili.com"}:
        return "B站视频"
    return "网页"




def decode_clipboard_bytes(value: bytes) -> str:
    if not value:
        return ""
    if value.startswith((b"\xff\xfe", b"\xfe\xff")):
        return value.decode("utf-16", errors="replace").rstrip("\x00")
    if b"\x00" in value[:64]:
        try:
            return value.decode("utf-16-le").rstrip("\x00")
        except UnicodeDecodeError:
            pass
    return value.decode("utf-8-sig", errors="replace").rstrip("\x00")


def parse_cf_html(value: bytes) -> tuple[str, str, str]:
    full_text = decode_clipboard_bytes(value)
    header = value[: min(len(value), 4096)].decode("ascii", errors="ignore")
    source_match = re.search(r"^SourceURL:(.+)$", header, flags=re.IGNORECASE | re.MULTILINE)
    source_url = source_match.group(1).strip() if source_match else ""
    start_match = re.search(r"StartFragment:(\d+)", header, flags=re.IGNORECASE)
    end_match = re.search(r"EndFragment:(\d+)", header, flags=re.IGNORECASE)
    if start_match and end_match:
        start = int(start_match.group(1))
        end = int(end_match.group(1))
        if 0 <= start < end <= len(value):
            return decode_clipboard_bytes(value[start:end]), full_text, source_url
    marker_match = re.search(
        r"<!--StartFragment-->(.*?)<!--EndFragment-->",
        full_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return (marker_match.group(1) if marker_match else full_text), full_text, source_url


class RichTextMarkdownParser(HTMLParser):
    """Small dependency-free converter for clipboard HTML fragments."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.lists: list[dict[str, int | str]] = []
        self.links: list[str] = []
        self.pre_depth = 0
        self.inline_code_depth = 0
        self.blockquote_depth = 0

    def _tail(self) -> str:
        return "".join(self.parts[-8:])

    def _newlines(self, count: int) -> None:
        if not self.parts:
            return
        existing = len(self._tail()) - len(self._tail().rstrip("\n"))
        if existing < count:
            self.parts.append("\n" * (count - existing))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): value or "" for key, value in attrs}
        if tag in {"p", "div", "section", "article", "header", "footer", "table"}:
            self._newlines(2)
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._newlines(2)
            self.parts.append("#" * int(tag[1]) + " ")
        elif tag == "br":
            self.parts.append("\n" + ("> " * self.blockquote_depth))
        elif tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("*")
        elif tag in {"del", "s", "strike"}:
            self.parts.append("~~")
        elif tag == "pre":
            self._newlines(2)
            language = ""
            class_name = attributes.get("class", "")
            language_match = re.search(r"(?:language-|lang-)([\w+-]+)", class_name)
            if language_match:
                language = language_match.group(1)
            self.parts.append(f"```{language}\n")
            self.pre_depth += 1
        elif tag == "code" and not self.pre_depth:
            self.parts.append("`")
            self.inline_code_depth += 1
        elif tag in {"ul", "ol"}:
            self._newlines(1)
            self.lists.append({"tag": tag, "index": 0})
        elif tag == "li":
            self._newlines(1)
            indent = "  " * max(0, len(self.lists) - 1)
            if self.lists and self.lists[-1]["tag"] == "ol":
                self.lists[-1]["index"] = int(self.lists[-1]["index"]) + 1
                marker = f"{self.lists[-1]['index']}. "
            else:
                marker = "- "
            self.parts.append(indent + marker)
        elif tag == "a":
            self.parts.append("[")
            self.links.append(attributes.get("href", ""))
        elif tag == "img":
            source = attributes.get("src", "")
            alt = attributes.get("alt", "图片")
            if source and not source.lower().startswith("data:"):
                self.parts.append(f"![{alt}]({source})")
        elif tag == "blockquote":
            self._newlines(2)
            self.blockquote_depth += 1
            self.parts.append("> " * self.blockquote_depth)
        elif tag in {"tr"}:
            self._newlines(1)
            self.parts.append("| ")
        elif tag in {"th", "td"}:
            if self.parts and not self._tail().endswith(("| ", "\n")):
                self.parts.append("| ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"p", "div", "section", "article", "header", "footer"}:
            self._newlines(2)
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._newlines(2)
        elif tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("*")
        elif tag in {"del", "s", "strike"}:
            self.parts.append("~~")
        elif tag == "pre":
            self.pre_depth = max(0, self.pre_depth - 1)
            self._newlines(1)
            self.parts.append("```")
            self._newlines(2)
        elif tag == "code" and self.inline_code_depth:
            self.parts.append("`")
            self.inline_code_depth -= 1
        elif tag in {"ul", "ol"}:
            if self.lists:
                self.lists.pop()
            self._newlines(1 if self.lists else 2)
        elif tag == "li":
            self._newlines(1)
        elif tag == "a":
            href = self.links.pop() if self.links else ""
            self.parts.append(f"]({href})" if href else "]")
        elif tag == "blockquote":
            self.blockquote_depth = max(0, self.blockquote_depth - 1)
            self._newlines(2)
        elif tag in {"th", "td"}:
            self.parts.append(" | ")
        elif tag in {"tr", "table"}:
            self._newlines(1 if tag == "tr" else 2)

    def handle_data(self, data: str) -> None:
        if not data:
            return
        if self.pre_depth:
            self.parts.append(data.replace("```", "`\u200b``"))
            return
        data = re.sub(r"[\t\r\f\v ]+", " ", data)
        data = re.sub(r" *\n+ *", " ", data)
        self.parts.append(data)

    def markdown(self) -> str:
        value = "".join(self.parts)
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()


def html_to_markdown(value: str) -> str:
    parser = RichTextMarkdownParser()
    parser.feed(value)
    parser.close()
    return parser.markdown()


def contain_markdown_fragment(value: str) -> tuple[str, bool]:
    """Prevent a captured fragment from changing the structure of later card content."""
    output: list[str] = []
    fence_char = ""
    fence_length = 0
    changed = False
    fence_pattern = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")
    heading_pattern = re.compile(r"^( {0,3})#{1,6}[ \t]+(.+?)\s*$")

    for line in value.splitlines():
        fence_match = fence_pattern.match(line)
        if fence_char:
            output.append(line)
            if (
                fence_match
                and fence_match.group(2)[0] == fence_char
                and len(fence_match.group(2)) >= fence_length
                and not fence_match.group(3).strip()
            ):
                fence_char = ""
                fence_length = 0
            continue

        if fence_match:
            marker = fence_match.group(2)
            fence_char = marker[0]
            fence_length = len(marker)
            output.append(line)
            continue

        heading_match = heading_pattern.match(line)
        if heading_match:
            heading = re.sub(r"[ \t]+#+[ \t]*$", "", heading_match.group(2)).strip()
            if heading:
                output.append(f"{heading_match.group(1)}**{heading}**")
                changed = True
                continue
        output.append(line)

    if fence_char:
        output.append(fence_char * fence_length)
        changed = True
    return "\n".join(output).rstrip(), changed




def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_fingerprint(payload: ClipboardPayload) -> str:
    digest = hashlib.sha256()
    digest.update(payload.kind.encode("utf-8"))
    if payload.kind == "image":
        image = payload.value
        digest.update(str((image.mode, image.size)).encode("ascii"))
        digest.update(image.tobytes())
    elif payload.kind == "files":
        for path in payload.value:
            digest.update(str(Path(path).resolve()).encode("utf-8", errors="replace"))
            digest.update(b"\x00")
    else:
        digest.update(str(payload.value).encode("utf-8", errors="replace"))
    return digest.hexdigest()


def should_suppress_exact_duplicate(
    fingerprint: str,
    last_fingerprint: str,
    now: float,
    last_accepted_at: float,
    duplicate_seconds: float,
    suppress_consecutive: bool,
) -> bool:
    if not fingerprint or fingerprint != last_fingerprint:
        return False
    return suppress_consecutive or (
        duplicate_seconds > 0 and now - last_accepted_at <= duplicate_seconds
    )




class CaptureSession:
    def __init__(
        self,
        root: Path,
        now: datetime | None = None,
        keep_raw_html: bool = True,
        write_machine_metadata: bool = True,
        options: StorageOptions | None = None,
    ) -> None:
        ensure_target_vault(root)
        self.root = root.resolve()
        self.options = options or StorageOptions()
        self.capture_root = self.options.capture_root(self.root)
        self.started = now or datetime.now()
        self.keep_raw_html = keep_raw_html
        self.write_machine_metadata = write_machine_metadata
        self.session_dir: Path | None = None
        self.card_path: Path | None = None
        self.attachment_dir: Path | None = None
        self.folder_prefix = self.started.strftime("%Y-%m-%d %H%M") if self.options.timestamp_prefix else ""
        self.item_count = 0
        self.entries: list[CapturedBlock] = []
        self.last_note: PersonalNoteBlock | None = None
        self.active = False
        self.resumed = False
        self.original_status = "收集中"
        self.card_id = str(uuid.uuid4())
        self.run_id = str(uuid.uuid4())
        self.has_prior_content = False

    def _prepare_append(self, note=False):
        if self.entries and file_sha256(self.card_path) != self.entries[-1].expected_card_hash:
            if note:
                raise RuntimeError("资料卡已被外部编辑，不能安全接续旧感想。请先收集新内容，或在编辑器中手动添加感想。")
            self.entries.clear()
            self.last_note = None

    def start(self) -> Path:
        parent = self.capture_root
        if self.options.group_by_month:
            parent = parent / self.started.strftime("%Y") / self.started.strftime("%m")
        base = (self.folder_prefix + " - " if self.folder_prefix else "") + "待命名资料卡"
        session_dir = parent / base
        suffix = 2
        while session_dir.exists():
            session_dir = parent / f"{base} ({suffix})"
            suffix += 1
        session_dir.mkdir(parents=True)
        attachment_dir = session_dir / "附件"
        attachment_dir.mkdir()
        card_path = session_dir / "待命名资料卡.md"
        timestamp = self.started.strftime("%Y-%m-%d %H:%M:%S")
        text = render_template(self.options.template_body, timestamp, self.card_id)
        card_path.write_text(text, encoding="utf-8", newline="\n")
        self.session_dir = session_dir
        self.attachment_dir = attachment_dir
        self.card_path = card_path
        self.active = True
        return card_path

    @classmethod
    def resume(
        cls,
        root: Path,
        card_path: Path,
        now: datetime | None = None,
        keep_raw_html: bool = True,
        write_machine_metadata: bool = True,
        options: StorageOptions | None = None,
    ) -> "CaptureSession":
        session = cls(
            root,
            now,
            keep_raw_html=keep_raw_html,
            write_machine_metadata=write_machine_metadata,
            options=options,
        )
        root_resolved = root.resolve()
        capture_root = session.capture_root
        candidate = card_path.resolve()
        if not candidate.is_file() or candidate.suffix.lower() != ".md":
            raise FileNotFoundError(f"资料卡不存在：{card_path}")
        if not candidate.is_relative_to(capture_root) or not candidate.is_relative_to(root_resolved):
            raise ValueError("只能继续收集到当前设置的收集目录中的 Markdown 资料卡")
        candidate_text = candidate.read_text(encoding="utf-8")
        if not candidate_text.startswith("---\n"):
            raise ValueError("所选文件不是带属性的资料卡")
        session.session_dir = candidate.parent
        session.card_path = candidate
        session.attachment_dir = candidate.parent / "附件"
        session.attachment_dir.mkdir(exist_ok=True)
        prefix_match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{4})\s+-\s+", candidate.parent.name)
        session.folder_prefix = prefix_match.group(1) if prefix_match else ""
        session.original_status = frontmatter_value(candidate, "状态") or "待整理"
        card_match = re.search(r"<!--\s*gs-card\s+(\{.*?\})\s*-->", candidate_text)
        if card_match:
            try:
                parsed_card = json.loads(card_match.group(1))
                session.card_id = str(parsed_card.get("card_id") or session.card_id)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        session.has_prior_content = session._card_has_collection_content(candidate_text)
        session.resumed = True
        session.active = True
        return session

    @staticmethod
    def _card_has_collection_content(text: str) -> bool:
        headings = ("**收集内容**", "## 收集内容")
        for heading in headings:
            position = text.rfind(heading)
            if position >= 0:
                tail = text[position + len(heading) :]
                tail = re.sub(r"<!--\s*gs-card\s+.*?-->", "", tail, flags=re.DOTALL)
                return bool(tail.strip())
        return False

    def _require_active(self) -> tuple[Path, Path]:
        if not self.active or not self.card_path or not self.attachment_dir:
            raise RuntimeError("当前没有正在收集的资料组")
        return self.card_path, self.attachment_dir

    def _record_primary_source(self, url: str) -> None:
        card_path, _ = self._require_active()
        if frontmatter_value(card_path, "上游来源") or frontmatter_value(card_path, "原文链接"):
            return
        if frontmatter_has_field(card_path, "上游来源"):
            updates = {"上游来源": json.dumps([url], ensure_ascii=False)}
        else:
            updates = {"原文链接": yaml_quote(url)}
        if frontmatter_value(card_path, "来源类型") in {"", "待判断"}:
            updates["来源类型"] = infer_source_type(url)
        self._update_frontmatter_preserving_offsets(updates)

    def _update_frontmatter_preserving_offsets(self, updates: dict[str, str]) -> None:
        card_path, _ = self._require_active()
        self._prepare_append()
        before_size = card_path.stat().st_size
        update_frontmatter(card_path, updates)
        delta = card_path.stat().st_size - before_size
        if delta:
            for entry in self.entries:
                entry.start_offset += delta
            if self.last_note:
                self.last_note.start_offset += delta
        if self.entries:
            self._refresh_expected_suffix(card_path, self.entries[-1])

    def _record_source_context(self, source_app_hint: str, source_url: str) -> None:
        if re.fullmatch(r"https?://\S+", source_url, flags=re.IGNORECASE):
            self._record_primary_source(source_url)
            return
        card_path, _ = self._require_active()
        if frontmatter_value(card_path, "来源类型") not in {"", "待判断"}:
            return
        source_type = SOURCE_APP_TYPES.get(Path(source_app_hint).name.lower())
        if source_type:
            self._update_frontmatter_preserving_offsets({"来源类型": source_type})

    @staticmethod
    def _visible_label(kind: str) -> str:
        if kind in {"文字", "链接", "Markdown", "富文本"}:
            return "〔摘录〕"
        if kind == "图片／截图":
            return "〔截图〕"
        if kind == "文件":
            return "〔文件〕"
        return "〔摘录〕"

    def _event_metadata(
        self,
        *,
        event_id: str,
        machine_kind: str,
        body: str,
        captured_at: datetime | None,
        attachment_paths: list[Path],
        source_app_hint: str,
        source_url: str,
        foreground_app_hint: str,
        foreground_window_title_hint: str,
        image_dimensions: tuple[int, int] | None,
        raw_format: str,
        capture_method: str,
        author: str,
        relates_to: str = "",
        source_body: str | None = None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "v": 1,
            "event_id": event_id,
            "card_id": self.card_id,
            "run_id": self.run_id,
            "run_seq": self.item_count + 1,
            "kind": machine_kind,
            "author": author,
            "captured_at": utc_timestamp(captured_at),
            "capture_method": capture_method,
            "source_app_hint": source_app_hint or "unknown",
            "source_url": source_url or "unknown",
            "raw_format": raw_format or "unknown",
            "content_sha256": hashlib.sha256(body.rstrip().encode("utf-8")).hexdigest(),
        }
        raw_refs = [f"附件/{path.name}" for path in attachment_paths]
        if len(raw_refs) == 1:
            metadata["raw_ref"] = raw_refs[0]
        elif raw_refs:
            metadata["raw_refs"] = raw_refs
        if machine_kind == "image" and len(attachment_paths) == 1:
            metadata["attachment_sha256"] = file_sha256(attachment_paths[0])
            if image_dimensions:
                metadata["image_width"], metadata["image_height"] = image_dimensions
        if foreground_app_hint:
            metadata["foreground_app_hint"] = foreground_app_hint
        if foreground_window_title_hint:
            metadata["foreground_window_title_hint"] = foreground_window_title_hint
        if relates_to:
            metadata["relates_to"] = relates_to
        if source_body is not None and source_body.rstrip() != body.rstrip():
            metadata["source_sha256"] = hashlib.sha256(
                source_body.rstrip().encode("utf-8")
            ).hexdigest()
            metadata["normalized_markdown"] = True
        return metadata

    @staticmethod
    def _append_prefix(card_path: Path, include_divider: bool) -> str:
        size = card_path.stat().st_size
        with card_path.open("rb") as existing:
            existing.seek(max(0, size - 2))
            ending = existing.read()
        if not ending:
            prefix = ""
        elif ending.endswith(b"\n\n"):
            prefix = ""
        elif ending.endswith(b"\n"):
            prefix = "\n"
        else:
            prefix = "\n\n"
        if include_divider:
            prefix += "---\n\n"
        return prefix

    @staticmethod
    def _refresh_expected_suffix(card_path: Path, entry: CapturedBlock) -> None:
        with card_path.open("rb") as handle:
            handle.seek(entry.start_offset)
            entry.expected_suffix = handle.read()
        entry.expected_card_hash = file_sha256(card_path)

    def _append(
        self,
        kind: str,
        body: str,
        captured_at: datetime | None = None,
        attachment_paths: list[Path] | None = None,
        *,
        machine_kind: str = "unknown",
        source_app_hint: str = "",
        source_url: str = "",
        foreground_app_hint: str = "",
        foreground_window_title_hint: str = "",
        image_dimensions: tuple[int, int] | None = None,
        raw_format: str = "unknown",
        capture_method: str = "automatic",
        author: str = "unknown",
        source_body: str | None = None,
    ) -> None:
        card_path, _ = self._require_active()
        body = body.rstrip()
        self._prepare_append()
        attachments = list(attachment_paths or [])
        self._record_source_context(source_app_hint, source_url)
        start_offset = card_path.stat().st_size
        event_id = str(uuid.uuid4())
        visible = f"{self._visible_label(kind)}\n\n{body}"
        rendered = visible
        if self.write_machine_metadata:
            try:
                metadata = self._event_metadata(
                    event_id=event_id,
                    machine_kind=machine_kind,
                    body=body,
                    captured_at=captured_at,
                    attachment_paths=attachments,
                    source_app_hint=source_app_hint,
                    source_url=source_url,
                    foreground_app_hint=foreground_app_hint,
                    foreground_window_title_hint=foreground_window_title_hint,
                    image_dimensions=image_dimensions,
                    raw_format=raw_format,
                    capture_method=capture_method,
                    author=author,
                    source_body=source_body,
                )
                rendered = (
                    f"<!-- gs-capture:start {compact_json(metadata)} -->\n\n"
                    f"{visible}\n\n"
                    f"<!-- gs-capture:end {compact_json({'event_id': event_id})} -->"
                )
            except Exception:
                LOGGER.exception("machine metadata generation failed; visible capture preserved")
        prefix = self._append_prefix(card_path, include_divider=self.has_prior_content)
        append_atomic(card_path, prefix + rendered + "\n\n")
        self.item_count += 1
        entry = CapturedBlock(start_offset, kind, attachments, b"", event_id)
        self._refresh_expected_suffix(card_path, entry)
        self.entries.append(entry)
        self.last_note = None
        self.has_prior_content = True
        LOGGER.info("capture saved kind=%s method=%s", machine_kind, capture_method)

    def add_text(
        self,
        text: str,
        captured_at: datetime | None = None,
        *,
        source_app_hint: str = "",
        source_url: str = "",
        raw_format: str = "CF_UNICODETEXT",
        capture_method: str = "automatic",
    ) -> None:
        stripped = text.strip()
        if not stripped:
            return
        is_single_url = bool(re.fullmatch(r"https?://\S+", stripped, flags=re.IGNORECASE))
        if is_single_url:
            self._record_primary_source(stripped)
            body = f"- 原始链接：<{stripped}>"
            kind = "链接"
            source_url = stripped
            source_body = None
            attachments: list[Path] = []
        else:
            source_body = text.rstrip()
            body, normalized = contain_markdown_fragment(source_body)
            kind = "文字"
            attachments = []
            if normalized:
                destination = self._unique_attachment_path(
                    f"原始文本-{self.item_count + 1:03d}",
                    ".txt",
                )
                destination.write_text(source_body, encoding="utf-8", newline="\n")
                attachments.append(destination)
        try:
            self._append(
                kind,
                body,
                captured_at,
                attachments,
                machine_kind="link" if is_single_url else "text",
                source_app_hint=source_app_hint,
                source_url=source_url,
                raw_format=raw_format,
                capture_method=capture_method,
                source_body=source_body,
            )
        except Exception:
            for attachment in attachments:
                attachment.unlink(missing_ok=True)
            raise

    def _unique_attachment_path(self, stem: str, suffix: str) -> Path:
        _, attachment_dir = self._require_active()
        candidate = attachment_dir / f"{stem}{suffix}"
        number = 2
        while candidate.exists():
            candidate = attachment_dir / f"{stem}-{number}{suffix}"
            number += 1
        return candidate

    def add_markdown(
        self,
        markdown: str,
        captured_at: datetime | None = None,
        raw_html: str | None = None,
        *,
        source_app_hint: str = "",
        source_url: str = "",
        raw_format: str = "text/markdown",
        capture_method: str = "automatic",
    ) -> None:
        stripped = markdown.strip()
        if not stripped:
            return
        if re.fullmatch(r"https?://\S+", source_url, flags=re.IGNORECASE):
            self._record_primary_source(source_url)
        source_body = stripped
        contained, normalized = contain_markdown_fragment(source_body)
        attachments: list[Path] = []
        if raw_html and self.keep_raw_html:
            destination = self._unique_attachment_path(
                f"原始富文本-{self.item_count + 1:03d}",
                ".html",
            )
            destination.write_text(raw_html, encoding="utf-8", newline="\n")
            attachments.append(destination)
        elif normalized:
            destination = self._unique_attachment_path(
                f"原始Markdown-{self.item_count + 1:03d}",
                ".txt",
            )
            destination.write_text(source_body, encoding="utf-8", newline="\n")
            attachments.append(destination)
        try:
            self._append(
                "富文本" if raw_html else "Markdown",
                contained,
                captured_at,
                attachments,
                machine_kind="rich_text" if raw_html else "markdown",
                source_app_hint=source_app_hint,
                source_url=source_url,
                raw_format=raw_format,
                capture_method=capture_method,
                source_body=source_body,
            )
        except Exception:
            for attachment in attachments:
                attachment.unlink(missing_ok=True)
            raise

    def add_image(
        self,
        image: Image.Image,
        captured_at: datetime | None = None,
        *,
        source_app_hint: str = "",
        source_url: str = "",
        foreground_app_hint: str = "",
        foreground_window_title_hint: str = "",
        raw_format: str = "image/png",
        capture_method: str = "automatic",
    ) -> None:
        _, attachment_dir = self._require_active()
        next_number = self.item_count + 1
        destination = self._unique_attachment_path(f"截图-{next_number:03d}", ".png")
        filename = destination.name
        image.save(destination, format="PNG")
        try:
            self._append(
                "图片／截图",
                f"![[附件/{filename}]]",
                captured_at,
                attachment_paths=[destination],
                machine_kind="image",
                source_app_hint=source_app_hint,
                source_url=source_url,
                foreground_app_hint=foreground_app_hint,
                foreground_window_title_hint=foreground_window_title_hint,
                image_dimensions=image.size,
                raw_format=raw_format,
                capture_method=capture_method,
            )
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    def add_files(
        self,
        paths: list[Path],
        captured_at: datetime | None = None,
        *,
        source_app_hint: str = "",
        source_url: str = "",
        raw_format: str = "CF_HDROP",
        capture_method: str = "automatic",
    ) -> None:
        _, attachment_dir = self._require_active()
        event_number = self.item_count + 1
        lines: list[str] = []
        copied_paths: list[Path] = []
        for index, source in enumerate(paths, start=1):
            source = source.expanduser()
            if not source.exists():
                lines.append(f"- 原路径已不可用：`{source}`")
                continue
            if source.is_dir():
                lines.append(f"- 文件夹引用（未递归复制）：`{source.resolve()}`")
                continue
            prefix = f"文件-{event_number:03d}-{index:02d}"
            destination = self._unique_attachment_path(
                f"{prefix}-{safe_name(source.stem, 60)}",
                source.suffix.lower(),
            )
            shutil.copy2(source, destination)
            copied_paths.append(destination)
            rel = f"附件/{destination.name}"
            if destination.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
                lines.append(f"![[{rel}]]")
            else:
                lines.append(f"- [[{rel}|{destination.name}]]")
        try:
            self._append(
                "文件",
                "\n".join(lines) or "- 未获得可用文件",
                captured_at,
                attachment_paths=copied_paths,
                machine_kind="files",
                source_app_hint=source_app_hint,
                source_url=source_url,
                raw_format=raw_format,
                capture_method=capture_method,
            )
        except Exception:
            for copied in copied_paths:
                copied.unlink(missing_ok=True)
            raise

    def add_payload(
        self,
        payload: ClipboardPayload,
        captured_at: datetime | None = None,
        *,
        source_app_hint: str = "",
        foreground_app_hint: str = "",
        foreground_window_title_hint: str = "",
        capture_method: str = "automatic",
    ) -> None:
        context = {
            "source_app_hint": source_app_hint,
            "source_url": payload.source_url,
            "raw_format": payload.raw_format,
            "capture_method": capture_method,
        }
        if payload.kind == "text":
            self.add_text(str(payload.value), captured_at, **context)
        elif payload.kind in {"markdown", "rich_text"}:
            self.add_markdown(str(payload.value), captured_at, raw_html=payload.raw_html, **context)
        elif payload.kind == "image":
            self.add_image(
                payload.value,
                captured_at,
                foreground_app_hint=foreground_app_hint,
                foreground_window_title_hint=foreground_window_title_hint,
                **context,
            )
        elif payload.kind == "files":
            self.add_files(list(payload.value), captured_at, **context)
        else:
            raise ValueError(f"不支持的收集类型：{payload.kind}")

    @staticmethod
    def _render_personal_note(body: str, metadata: dict[str, Any] | None) -> str:
        visible = f"〔我的想法〕{body}" if "\n" not in body else f"〔我的想法〕\n\n{body}"
        if metadata is None:
            return visible
        event_id = str(metadata["event_id"])
        return (
            f"<!-- gs-capture:start {compact_json(metadata)} -->\n\n"
            f"{visible}\n\n"
            f"<!-- gs-capture:end {compact_json({'event_id': event_id})} -->"
        )

    @staticmethod
    def _refresh_note_expected_suffix(card_path: Path, note: PersonalNoteBlock) -> None:
        with card_path.open("rb") as handle:
            handle.seek(note.start_offset)
            note.expected_suffix = handle.read()

    def _continue_last_note(self, card_path: Path, stripped: str) -> bool:
        note = self.last_note
        if note is None or note.relates_to != self.entries[-1].event_id:
            return False
        with card_path.open("rb") as handle:
            handle.seek(note.start_offset)
            actual_suffix = handle.read()
        if actual_suffix != note.expected_suffix:
            LOGGER.warning("personal note continuation skipped because card tail changed")
            self.last_note = None
            return False

        combined = f"{note.body.rstrip()}\n\n{stripped}"
        metadata = dict(note.metadata) if note.metadata is not None else None
        if metadata is not None:
            continuation = {
                "event_id": str(uuid.uuid4()),
                "captured_at": utc_timestamp(),
                "content_sha256": hashlib.sha256(stripped.encode("utf-8")).hexdigest(),
            }
            continuations = list(metadata.get("continuations") or [])
            continuations.append(continuation)
            metadata["continuations"] = continuations
            metadata["revision_count"] = 1 + len(continuations)
            metadata["updated_at"] = continuation["captured_at"]
            metadata["content_sha256"] = hashlib.sha256(
                combined.rstrip().encode("utf-8")
            ).hexdigest()

        rendered = self._render_personal_note(combined, metadata)
        original = card_path.read_bytes()
        replace_atomic(card_path, original[:note.start_offset] + (rendered + "\n\n").encode("utf-8"), original)
        note.body = combined
        note.metadata = metadata
        self._refresh_note_expected_suffix(card_path, note)
        self._refresh_expected_suffix(card_path, self.entries[-1])
        LOGGER.info("personal note continued")
        return True

    def add_note(self, text: str) -> None:
        card_path, _ = self._require_active()
        self._prepare_append(note=True)
        stripped = text.strip()
        if not stripped:
            raise ValueError("个人感想不能为空")
        if not self.entries:
            raise RuntimeError("当前资料卡还没有可添加个人感想的内容")
        if self._continue_last_note(card_path, stripped):
            return

        event_id = str(uuid.uuid4())
        metadata: dict[str, Any] | None = None
        if self.write_machine_metadata:
            try:
                metadata = self._event_metadata(
                    event_id=event_id,
                    machine_kind="personal_note",
                    body=stripped,
                    captured_at=None,
                    attachment_paths=[],
                    source_app_hint="collector_note_dialog",
                    source_url="",
                    foreground_app_hint="",
                    foreground_window_title_hint="",
                    image_dimensions=None,
                    raw_format="text/plain",
                    capture_method="hotkey_note",
                    author="user",
                    relates_to=self.entries[-1].event_id,
                )
                metadata["run_seq"] = self.item_count
                metadata["revision_count"] = 1
            except Exception:
                metadata = None
                LOGGER.exception("personal note metadata generation failed; visible note preserved")
        rendered = self._render_personal_note(stripped, metadata)
        start_offset = card_path.stat().st_size
        append_atomic(card_path, rendered + "\n\n")
        note = PersonalNoteBlock(
            start_offset=start_offset,
            expected_suffix=b"",
            body=stripped,
            relates_to=self.entries[-1].event_id,
            metadata=metadata,
        )
        self._refresh_note_expected_suffix(card_path, note)
        self.last_note = note
        self._refresh_expected_suffix(card_path, self.entries[-1])
        LOGGER.info("personal note saved")

    def undo_last(self) -> tuple[str, int]:
        card_path, _ = self._require_active()
        if not self.entries:
            raise RuntimeError("当前没有可以撤销的收集内容")
        entry = self.entries[-1]
        with card_path.open("rb") as handle:
            handle.seek(entry.start_offset)
            actual_suffix = handle.read()
        if actual_suffix != entry.expected_suffix or file_sha256(card_path) != entry.expected_card_hash:
            LOGGER.warning("safe undo refused because card suffix changed")
            raise RuntimeError(
                "资料卡尾部已被 Obsidian、同步程序或其他操作修改；为避免误删，无法自动撤销。"
                "请在 Obsidian 中手动删除最近内容。"
            )
        original = card_path.read_bytes()
        replace_atomic(card_path, original[:entry.start_offset], original)
        self.entries.pop()
        self.last_note = None
        for attachment in entry.attachment_paths:
            try:
                attachment.unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("unused attachment retained after undo")
        self.item_count -= 1
        if self.entries:
            self._refresh_expected_suffix(card_path, self.entries[-1])
        self.has_prior_content = self._card_has_collection_content(card_path.read_text(encoding="utf-8"))
        LOGGER.info("capture undone kind=%s", entry.kind)
        return entry.kind, self.item_count

    def rename(self, title: str) -> Path:
        if not self.card_path or not self.attachment_dir or not self.session_dir:
            raise RuntimeError("当前还没有可命名的资料组")
        old_dir = self.session_dir
        old_card = self.card_path
        clean_title = safe_name(title, 100)
        candidate = clean_title
        suffix = 2
        prefix = self.folder_prefix
        while True:
            target_dir = old_dir.parent / (f"{prefix} - {candidate}" if prefix else candidate)
            target_card_in_old_dir = old_dir / f"{candidate}.md"
            dir_conflict = target_dir.exists() and target_dir != old_dir
            card_conflict = target_card_in_old_dir.exists() and target_card_in_old_dir != old_card
            if not dir_conflict and not card_conflict:
                break
            candidate = f"{clean_title} ({suffix})"
            suffix += 1

        renamed_card_in_old_dir = old_dir / f"{candidate}.md"
        card_was_renamed = False
        step = "准备重命名"
        try:
            if renamed_card_in_old_dir != old_card:
                step = "重命名 Markdown 文件"
                old_card.rename(renamed_card_in_old_dir)
                card_was_renamed = True
            if target_dir != old_dir:
                step = "重命名资料组文件夹"
                old_dir.rename(target_dir)
        except Exception as exc:
            rollback_error: Exception | None = None
            if card_was_renamed and renamed_card_in_old_dir.exists() and not old_card.exists():
                try:
                    renamed_card_in_old_dir.rename(old_card)
                except Exception as rollback_exc:
                    rollback_error = rollback_exc

            current_dir = target_dir if target_dir.exists() else old_dir
            current_cards = (
                current_dir / f"{candidate}.md",
                current_dir / old_card.name,
            )
            current_card = next((path for path in current_cards if path.exists()), None)
            if current_dir.exists() and current_card:
                self.session_dir = current_dir
                self.attachment_dir = current_dir / "附件"
                self.card_path = current_card

            error_code = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
            LOGGER.exception(
                "rename failed step=%s source_card=%s target_card=%s source_dir=%s "
                "target_dir=%s error_code=%s rollback_error=%r",
                step,
                old_card,
                renamed_card_in_old_dir,
                old_dir,
                target_dir,
                error_code,
                rollback_error,
            )
            code_text = f"（错误码 {error_code}）" if error_code is not None else ""
            retry_text = "关闭提示后，可再次使用原来的重命名按钮重试。"
            if rollback_error is not None:
                retry_text = "文件状态已重新定位；关闭提示后，可再次使用原来的重命名按钮重试。"
            raise RuntimeError(
                f"{step}失败{code_text}。\n目标：{target_dir}\n原因：{exc}\n{retry_text}"
            ) from exc

        self.session_dir = target_dir
        self.attachment_dir = target_dir / "附件"
        self.card_path = target_dir / f"{candidate}.md"
        LOGGER.info("rename completed target_card=%s target_dir=%s", self.card_path, target_dir)
        return self.card_path

    def stop(self, stopped_at: datetime | None = None) -> Path:
        card_path, _ = self._require_active()
        stopped_at = stopped_at or datetime.now()
        if not self.resumed or self.original_status == "收集中":
            update_frontmatter(card_path, {"状态": "待整理"})
        self.active = False
        LOGGER.info("capture session stopped resumed=%s items=%d", self.resumed, self.item_count)
        return card_path


def update_frontmatter(path: Path, updates: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"卡片缺少 YAML：{path}")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError(f"卡片 YAML 未闭合：{path}")
    frontmatter = text[4:end]
    for field, value in updates.items():
        pattern = re.compile(rf"^{re.escape(field)}:.*$", flags=re.MULTILINE)
        replacement = f"{field}: {value}"
        if pattern.search(frontmatter):
            frontmatter = pattern.sub(replacement, frontmatter, count=1)
        else:
            frontmatter += "\n" + replacement
    updated = "---\n" + frontmatter + text[end:]
    replace_atomic(path, updated.encode("utf-8"), text.encode("utf-8"), normalize_newlines=True)


def replace_atomic(path: Path, data: bytes, expected: bytes, normalize_newlines=False):
    fd, name = tempfile.mkstemp(prefix=".gs-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        current = path.read_bytes()
        if normalize_newlines:
            current = current.replace(b"\r\n", b"\n")
        if current != expected:
            raise RuntimeError("资料卡在写入期间被外部修改，请停止其他编辑后重试。")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def append_atomic(path: Path, text: str):
    original = path.read_bytes()
    replace_atomic(path, original + text.encode("utf-8"), original)



