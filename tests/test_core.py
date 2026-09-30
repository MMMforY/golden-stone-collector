from datetime import datetime
from pathlib import Path
import pytest
from golden_stone_collector.core import CaptureSession,StorageOptions,safe_name


def test_custom_root_template_naming(tmp_path):
    session=CaptureSession(tmp_path,datetime(2026,1,2,3,4,56),options=StorageOptions("inbox",False,False,"### 自定义说明"))
    card=session.start()
    assert "### 自定义说明" in card.read_text(encoding="utf8")
    assert card.parent.parent == tmp_path/"inbox"
    renamed=session.rename("新标题")
    assert renamed == tmp_path/"inbox/新标题/新标题.md"
    assert safe_name("CON") != "CON"


@pytest.mark.parametrize("location",["prefix","suffix"])
def test_undo_external_edit_refused(tmp_path,location):
    session=CaptureSession(tmp_path)
    card=session.start()
    session.add_text("a")
    original=card.read_text(encoding="utf8")
    modified=original.replace("### 资料说明","### 外部修改") if location=="prefix" else original+"external"
    card.write_text(modified,encoding="utf8")
    with pytest.raises(RuntimeError): session.undo_last()
    assert card.read_text(encoding="utf8")==modified


def test_rename_failure_rollback(tmp_path,monkeypatch):
    session=CaptureSession(tmp_path)
    card=session.start()
    session.add_text("保持原文")
    before=card.read_bytes()
    original_rename=Path.rename
    def fail_directory(self,target):
        if self.is_dir(): raise PermissionError("folder busy")
        return original_rename(self,target)
    with monkeypatch.context() as patch:
        patch.setattr(Path,"rename",fail_directory)
        with pytest.raises(RuntimeError): session.rename("改名")
    assert session.card_path == card and card.read_bytes()==before
    result=session.rename("改名")
    assert result.stem=="改名"


def test_atomic_write_failure_preserves_note(tmp_path,monkeypatch):
    import golden_stone_collector.core as core
    session=CaptureSession(tmp_path)
    card=session.start()
    session.add_text("a")
    session.add_note("第一段")
    original=card.read_bytes()
    def fail(*args): raise PermissionError("read only")
    monkeypatch.setattr(core.os,"replace",fail)
    with pytest.raises(PermissionError): session.add_note("第二段")
    assert card.read_bytes()==original
    assert not list(card.parent.glob(".gs-*.tmp"))


def test_external_edit_then_new_capture_drops_old_undo(tmp_path):
    session=CaptureSession(tmp_path)
    card=session.start()
    session.add_text("第一项")
    card.write_text(card.read_text(encoding="utf8").replace("### 资料说明","### 改过的说明"),encoding="utf8")
    with pytest.raises(RuntimeError): session.add_note("不安全的感想")
    session.add_text("第二项")
    assert len(session.entries)==1
    session.undo_last()
    with pytest.raises(RuntimeError): session.undo_last()
    text=card.read_text(encoding="utf8")
    assert "改过的说明" in text and "第一项" in text and "第二项" not in text
