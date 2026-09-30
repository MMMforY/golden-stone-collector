from dataclasses import asdict
import json
import pytest
from golden_stone_collector.config import Config, ConfigStore, from_dict, import_legacy, current_platform
from golden_stone_collector.core import StorageOptions
from golden_stone_collector.hotkeys import HotkeyManager,parse_shortcut,validate_bindings
from conftest import FakeKeys


def test_export_import_portable_and_backup(tmp_path):
    store = ConfigStore(tmp_path / "config")
    config = Config(vault_path=str(tmp_path),recent_vaults=[str(tmp_path)],launch_at_login=True)
    store.save(config)
    exported = tmp_path / "portable.json"
    store.export(exported,config)
    assert str(tmp_path).replace("\\","\\\\") not in exported.read_text(encoding="utf8")
    imported = store.import_file(exported)
    assert imported.vault_path == "" and not imported.launch_at_login
    imported.vault_path = str(tmp_path)
    store.save(imported,backup=True)
    assert len(list(store.directory.glob("*.before-*.json"))) == 1
    assert store.load() == imported


def test_legacy_import_separate_platform():
    config = import_legacy({"启动快捷键":"Ctrl+Alt+1","备注快捷键":"Ctrl+Alt+3","窗口置顶":True})
    assert config.hotkeys["windows"]["start"] == "Ctrl+Alt+1"
    assert config.hotkeys["macos"]["start"] == "Cmd+Option+1"
    assert config.always_on_top


@pytest.mark.parametrize("path",["../escape","/absolute","C:/absolute","..","folder/../../x","",r"..\x"])
def test_path_escape_refused(tmp_path,path):
    with pytest.raises(ValueError):
        StorageOptions(capture_directory=path).capture_root(tmp_path)


def test_invalid_config():
    with pytest.raises(ValueError): from_dict({"version":999})
    with pytest.raises(ValueError): from_dict({"poll_ms":1})
    with pytest.raises(ValueError): from_dict({"template_body":"<!-- gs-card {} -->"})
    with pytest.raises(ValueError): from_dict({"unknown":1})


def test_hotkey_transaction():
    config = Config()
    backend = FakeKeys()
    manager = HotkeyManager(backend,"windows")
    previous = config.hotkeys["windows"]
    manager.replace(previous)
    desired = dict(previous,start="Ctrl+Alt+1")
    backend.fail = parse_shortcut(desired["start"],"windows").text
    with pytest.raises(RuntimeError,match="已保留原配置"):
        manager.replace(desired)
    assert manager.bindings == previous
    assert len(backend.registered) == 5
    with pytest.raises(ValueError,match="重复"):
        validate_bindings(dict(previous,stop=previous["start"]),"windows")


@pytest.mark.parametrize("key",["A","Ctrl+Alt+F24","Cmd+1","Ctrl+A, Ctrl+B","Ctrl+Ctrl+A"])
def test_unsupported_shortcuts(key):
    with pytest.raises(ValueError): parse_shortcut(key,"windows")


def test_config_save_failure_rolls_back(controller,monkeypatch):
    c = controller
    before = c.config.clone()
    desired = before.clone()
    desired.hotkeys[current_platform()]["show"] = "Ctrl+Alt+Shift+7" if current_platform()=="windows" else "Cmd+Option+7"
    def fail(*args,**kwargs): raise PermissionError("disk read-only")
    monkeypatch.setattr(c.store,"save",fail)
    with pytest.raises(PermissionError): c.apply_config(desired)
    assert c.config == before
    assert c.hotkeys.bindings == before.hotkeys[current_platform()]
