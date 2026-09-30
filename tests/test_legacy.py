from golden_stone_collector.core import *
import tempfile

def test_legacy_regression() -> None:
    with tempfile.TemporaryDirectory(prefix="golden-stone-continuous-") as temp:
        root = Path(temp).resolve()

        external = root.parent / f"capture-test-{os.getpid()}.txt"
        external.write_text("外部文件保持不变", encoding="utf-8")
        original_bytes = external.read_bytes()
        try:
            session = CaptureSession(root, datetime(2026, 8, 21, 16, 0, 0))
            card = session.start()
            assert CAPTURE_ROOT_REL.as_posix() in card.relative_to(root).as_posix()
            assert "<!-- gs-card" in card.read_text(encoding="utf-8")
            session.add_text(
                "https://example.invalid/source",
                datetime(2026, 8, 21, 16, 0, 1),
                source_app_hint="browser.exe",
            )
            session.add_text(
                "完全相同的文字",
                datetime(2026, 8, 21, 16, 0, 2),
                source_app_hint="browser.exe",
            )
            active_named = session.rename("收集中标题")
            assert active_named.name == "收集中标题.md"
            assert active_named.parent.name == "2026-08-21 1600 - 收集中标题"
            session.add_note("这两项需要放在一起理解。")
            assert session.item_count == 2
            session.add_text("完全相同的文字", datetime(2026, 8, 21, 16, 0, 3))
            session.add_image(
                Image.new("RGB", (16, 16), "white"),
                datetime(2026, 8, 21, 16, 0, 4),
                foreground_app_hint="ChatGPT.exe",
                foreground_window_title_hint="合并主Vault",
            )
            session.add_files([external], datetime(2026, 8, 21, 16, 0, 5))
            copied_file = next(path for path in session.entries[-1].attachment_paths)
            assert copied_file.exists()
            undone_kind, remaining = session.undo_last()
            assert undone_kind == "文件"
            assert remaining == 4
            assert not copied_file.exists()
            session.add_files([external], datetime(2026, 8, 21, 16, 0, 6))
            stopped = session.stop(datetime(2026, 8, 21, 16, 1, 0))
            stopped = session.rename("统一测试标题")
            text = stopped.read_text(encoding="utf-8")
            assert "状态: 待整理" in text
            assert "来源类型: 网页" in text
            assert '上游来源: ["https://example.invalid/source"]' in text
            assert "会话状态:" not in text
            assert "捕获内容数:" not in text
            assert text.count("完全相同的文字") == 2
            assert stopped.name == "统一测试标题.md"
            assert stopped.parent.name == "2026-08-21 1600 - 统一测试标题"
            assert "〔我的想法〕这两项需要放在一起理解。" in text
            assert "> 个人感想：" not in text
            assert "这两项需要放在一起理解。" in text
            assert "```text" not in text
            assert "### 条目" not in text
            assert "｜16:00:" not in text
            assert "- [ ] 待回看" not in text
            assert "## 手动整理" not in text
            assert "### 下一步" not in text
            assert "### 资料说明" in text
            assert "### 为什么留下" in text
            assert "简短总结" not in text
            assert "当前理解" not in text
            assert "**收集内容**" in text
            assert text.count("〔摘录〕") == 3
            assert text.count("〔截图〕") == 1
            assert text.count("〔文件〕") == 1
            assert text.count("\n---\n") >= 5
            assert "![[附件/截图-004.png]]" in text
            assert len(list((stopped.parent / "附件").iterdir())) == 2
            assert external.read_bytes() == original_bytes
            assert not (root / "系统" / "知识整合" / "条目").exists()

            marker_values = re.findall(r"<!-- gs-capture:start (\{.*?\}) -->", text)
            assert len(marker_values) == 6  # 5 captures plus one personal note
            parsed_markers = [json.loads(value) for value in marker_values]
            assert all(marker["event_id"] for marker in parsed_markers)
            assert all(len(marker["content_sha256"]) == 64 for marker in parsed_markers)
            assert parsed_markers[0]["source_app_hint"] == "browser.exe"
            assert parsed_markers[0]["source_url"] == "https://example.invalid/source"
            image_marker = next(marker for marker in parsed_markers if marker["kind"] == "image")
            assert image_marker["foreground_app_hint"] == "ChatGPT.exe"
            assert image_marker["foreground_window_title_hint"] == "合并主Vault"
            assert image_marker["image_width"] == 16
            assert image_marker["image_height"] == 16
            image_path = stopped.parent / image_marker["raw_ref"]
            assert image_marker["attachment_sha256"] == file_sha256(image_path)
            note_marker = next(marker for marker in parsed_markers if marker["kind"] == "personal_note")
            assert note_marker["author"] == "user"
            assert note_marker["relates_to"] == parsed_markers[1]["event_id"]

            original_after_first_run = stopped.read_bytes()
            resumed = CaptureSession.resume(
                root,
                stopped,
                datetime(2026, 8, 22, 9, 0, 0),
                keep_raw_html=True,
            )
            resumed.add_text("仅用于验证撤销的续写")
            assert resumed.item_count == 1
            resumed.undo_last()
            assert stopped.read_bytes() == original_after_first_run
            rich_markdown = html_to_markdown(
                "<h2>AI 对话标题</h2><p><strong>重点</strong></p><ul><li>第一项</li><li>第二项</li></ul>"
            )
            assert "## AI 对话标题" in rich_markdown
            assert "**重点**" in rich_markdown
            assert "- 第一项" in rich_markdown
            contained_rich, rich_changed = contain_markdown_fragment(rich_markdown)
            assert rich_changed
            assert "**AI 对话标题**" in contained_rich
            assert "## AI 对话标题" not in contained_rich
            resumed.add_markdown(rich_markdown, raw_html="<h2>AI 对话标题</h2>")
            resumed_path = resumed.stop()
            resumed_text = resumed_path.read_text(encoding="utf-8")
            assert resumed_path == stopped
            assert resumed.item_count == 1
            assert contained_rich in resumed_text
            assert '"kind":"rich_text"' in resumed_text
            assert '"normalized_markdown":true' in resumed_text
            assert '"source_sha256"' in resumed_text
            assert "状态: 待整理" in resumed_text
            assert (resumed_path.parent / "附件" / "原始富文本-001.html").is_file()

            # A personal note is part of the preceding capture and is removed with it.
            note_session = CaptureSession(root, datetime(2026, 8, 21, 17, 0, 0))
            note_card = note_session.start()
            note_session.add_text("带感想的摘录")
            note_session.add_note("我的连续想法")
            note_session.undo_last()
            note_text = note_card.read_text(encoding="utf-8")
            assert "带感想的摘录" not in note_text
            assert "我的连续想法" not in note_text

            # Safe undo refuses to truncate after an external tail edit.
            safety_session = CaptureSession(root, datetime(2026, 8, 21, 18, 0, 0))
            safety_card = safety_session.start()
            safety_session.add_text("程序写入")
            with safety_card.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write("手动追加，不能丢失\n")
            safety_before = safety_card.read_bytes()
            try:
                safety_session.undo_last()
                raise AssertionError("safe undo should refuse after an external edit")
            except RuntimeError as exc:
                assert "无法自动撤销" in str(exc)
            assert safety_card.read_bytes() == safety_before

            # A resumed crash card transitions from 收集中 to 待整理 on stop.
            crash_session = CaptureSession(root, datetime(2026, 8, 21, 19, 0, 0))
            crash_card = crash_session.start()
            crash_session.add_text("崩溃前内容")
            recovered = CaptureSession.resume(root, crash_card, datetime(2026, 8, 21, 19, 5, 0))
            recovered.add_text("恢复后内容")
            recovered.stop()
            assert frontmatter_value(crash_card, "状态") == "待整理"

            # Resuming a card in a later workflow state preserves that state.
            update_frontmatter(resumed_path, {"状态": "待回看"})
            preserving = CaptureSession.resume(root, resumed_path, datetime(2026, 8, 21, 20, 0, 0))
            preserving.add_text("补充内容")
            preserving.stop()
            assert frontmatter_value(resumed_path, "状态") == "待回看"

            # Legacy/manual cards without capture markers remain appendable.
            legacy_dir = root / CAPTURE_ROOT_REL / "2026" / "08" / "2026-08-21 2100 - 旧卡"
            legacy_dir.mkdir(parents=True)
            legacy_card = legacy_dir / "旧卡.md"
            legacy_card.write_text(
                "---\n状态: 待整理\n---\n\n## 收集内容\n\n旧的手动内容\n",
                encoding="utf-8",
            )
            legacy = CaptureSession.resume(root, legacy_card, datetime(2026, 8, 21, 21, 1, 0))
            legacy.add_text("新续写")
            legacy.stop()
            assert "旧的手动内容" in legacy_card.read_text(encoding="utf-8")
            assert "<!-- gs-capture:start" in legacy_card.read_text(encoding="utf-8")

            # Metadata can be disabled without blocking visible Markdown capture.
            plain_session = CaptureSession(
                root,
                datetime(2026, 8, 21, 22, 0, 0),
                write_machine_metadata=False,
            )
            plain_card = plain_session.start()
            plain_session.add_text("纯 Markdown 模式")
            plain_text = plain_card.read_text(encoding="utf-8")
            assert "〔摘录〕" in plain_text
            assert "<!-- gs-capture:start" not in plain_text

            # Metadata failure must never block the visible capture.
            fallback_session = CaptureSession(root, datetime(2026, 8, 21, 23, 0, 0))
            fallback_card = fallback_session.start()
            original_metadata_builder = fallback_session._event_metadata
            try:
                def broken_metadata(**_kwargs: Any) -> dict[str, Any]:
                    raise RuntimeError("metadata test failure")

                fallback_session._event_metadata = broken_metadata  # type: ignore[method-assign]
                fallback_session.add_text("元数据失败仍需保存")
            finally:
                fallback_session._event_metadata = original_metadata_builder  # type: ignore[method-assign]
            fallback_text = fallback_card.read_text(encoding="utf-8")
            assert "元数据失败仍需保存" in fallback_text
            assert "<!-- gs-capture:start" not in fallback_text

            # Captured Markdown cannot leak headings or an open code fence into later card content.
            boundary_session = CaptureSession(root, datetime(2026, 8, 21, 23, 30, 0))
            boundary_card = boundary_session.start()
            unsafe_fragment = "### 外部标题\n\n正文\n\n```text\n未闭合代码"
            boundary_session.add_text(unsafe_fragment)
            boundary_text = boundary_card.read_text(encoding="utf-8")
            assert "**外部标题**" in boundary_text
            assert "### 外部标题" not in boundary_text
            assert boundary_text.count("```text") == 1
            assert boundary_text.count("\n```\n") == 1
            assert (boundary_card.parent / "附件" / "原始文本-001.txt").read_text(
                encoding="utf-8"
            ) == unsafe_fragment
            boundary_markers = [
                json.loads(value)
                for value in re.findall(r"<!-- gs-capture:start (\{.*?\}) -->", boundary_text)
            ]
            assert boundary_markers[0]["normalized_markdown"] is True
            assert len(boundary_markers[0]["source_sha256"]) == 64

            # Repeated F11 without a new capture continues one visible and machine note block.
            boundary_session.add_note("第一段想法")
            boundary_session.add_note("第二段补充")
            continued_text = boundary_card.read_text(encoding="utf-8")
            continued_markers = [
                json.loads(value)
                for value in re.findall(r"<!-- gs-capture:start (\{.*?\}) -->", continued_text)
            ]
            note_markers = [marker for marker in continued_markers if marker["kind"] == "personal_note"]
            assert len(note_markers) == 1
            assert continued_text.count("〔我的想法〕") == 1
            assert "第一段想法\n\n第二段补充" in continued_text
            assert "<!--" not in continued_text[
                continued_text.index("第一段想法") : continued_text.index("第二段补充")
            ]
            assert note_markers[0]["revision_count"] == 2
            assert len(note_markers[0]["continuations"]) == 1
            assert note_markers[0]["relates_to"] == continued_markers[0]["event_id"]

            # A new capture starts a new thought group and association.
            boundary_session.add_text("新的摘录")
            boundary_session.add_note("针对新摘录的想法")
            separated_text = boundary_card.read_text(encoding="utf-8")
            separated_markers = [
                json.loads(value)
                for value in re.findall(r"<!-- gs-capture:start (\{.*?\}) -->", separated_text)
            ]
            separated_notes = [marker for marker in separated_markers if marker["kind"] == "personal_note"]
            assert len(separated_notes) == 2
            assert separated_text.count("〔我的想法〕") == 2
            assert separated_notes[-1]["relates_to"] == next(
                marker["event_id"]
                for marker in reversed(separated_markers)
                if marker["kind"] == "text"
            )

            source_app_session = CaptureSession(root, datetime(2026, 8, 22, 0, 0, 0))
            source_app_card = source_app_session.start()
            source_app_session.add_text("来自桌面应用", source_app_hint="ChatGPT.exe")
            assert frontmatter_value(source_app_card, "来源类型") == "ChatGPT"
            assert frontmatter_value(source_app_card, "上游来源") == ""
        finally:
            external.unlink(missing_ok=True)

    cf_html = (
        b"Version:1.0\r\nSourceURL:https://example.invalid/rich\r\n"
        b"<!--StartFragment--><p>rich</p><!--EndFragment-->"
    )
    fragment, _full, source_url = parse_cf_html(cf_html)
    assert fragment == "<p>rich</p>"
    assert source_url == "https://example.invalid/rich"
    contained, changed = contain_markdown_fragment("### 标题\n\n正文\n\n```python\nprint('x')")
    assert changed
    assert contained == "**标题**\n\n正文\n\n```python\nprint('x')\n```"
    assert should_suppress_exact_duplicate("same", "same", 100.0, 1.0, 2.0, True)
    assert not should_suppress_exact_duplicate("same", "different", 100.0, 99.5, 2.0, True)
    assert should_suppress_exact_duplicate("same", "same", 100.0, 99.5, 2.0, False)
    assert not should_suppress_exact_duplicate("same", "same", 100.0, 1.0, 2.0, False)



    assert infer_source_type("https://mp.weixin.qq.com/s/example") == "微信公众号"
    assert infer_source_type("https://www.bilibili.com/video/BV1xx") == "B站视频"
    assert infer_source_type("https://example.com/article") == "网页"
    print("Continuous-collection self-test: PASS")


