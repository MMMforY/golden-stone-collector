import pytest
from golden_stone_collector.config import current_platform


def test_start_resume_no_backfill_manual_duplicate(controller):
    c = controller
    c.start()
    c.poll()
    assert c.session.item_count == 0
    c.clipboard.put("新的内容")
    c.poll()
    assert c.session.item_count == 1
    c.clipboard.put("新的内容")
    c.poll()
    assert c.session.item_count == 1
    c.toggle_pause()
    c.clipboard.put("暂停期间")
    c.poll()
    c.toggle_pause()
    c.poll()
    assert c.session.item_count == 1
    c.poll(manual=True)
    assert c.session.item_count == 2
    c.add_note("第一段")
    c.add_note("第二段")
    text = c.session.card_path.read_text(encoding="utf8")
    assert text.count("〔我的想法〕") == 1
    c.undo()
    assert c.session.item_count == 1
    path = c.finish("合成测试")
    assert path.stem == "合成测试" and "合成测试" in path.parent.name
    c.clipboard.put("旧卡启动前的剪贴板")
    c.start(path)
    c.poll()
    assert c.session.item_count == 0
    with pytest.raises(RuntimeError): c.undo()


def test_ignore_uses_source_not_foreground(controller):
    c=controller
    c.config.ignored_apps[current_platform()] = ["Typeless","foreground-only"]
    c.start()
    c.clipboard.put("语音临时内容","Typeless")
    c.poll()
    assert c.session.item_count == 0
    c.clipboard.put("未知写入者", "")
    c.poll()
    assert c.session.item_count == 1


def test_dialog_and_pause_do_not_backfill(controller):
    c=controller
    c.start()
    c.enter_dialog()
    c.clipboard.put("输入窗口中的临时剪贴板")
    c.poll()
    c.leave_dialog()
    c.poll()
    assert c.session.item_count == 0


def test_write_failure_never_success(controller,monkeypatch):
    c=controller
    messages, errors = [],[]
    c.saved.connect(messages.append)
    c.error.connect(errors.append)
    c.start()
    def fail(*args,**kwargs): raise PermissionError("模拟写入失败")
    monkeypatch.setattr(c.session,"add_payload",fail)
    c.clipboard.put("未保存")
    c.poll()
    assert c.paused and errors and not messages


def test_storage_change_blocked_appearance_live(controller):
    c=controller
    c.start()
    desired=c.config.clone()
    desired.capture_directory="other"
    with pytest.raises(ValueError): c.apply_config(desired)
    desired=c.config.clone()
    desired.always_on_top=True
    c.apply_config(desired)
    assert c.config.always_on_top


def test_missing_root(controller,tmp_path):
    controller.config.vault_path=str(tmp_path/"does-not-exist")
    with pytest.raises(ValueError): controller.start()
