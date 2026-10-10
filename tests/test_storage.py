import json

import pytest

from notifier import settings, storage


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "CHANNELS_FILE", str(tmp_path / "channels.json"))
    monkeypatch.setattr(storage, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(storage, "DEFAULT_CHANNELS", {"@builtin": "Built-in"})
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    storage.channels.clear()
    storage.options.clear()
    storage.state.clear()
    return tmp_path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_loads_legacy_string_and_object_entries(isolated):
    (isolated / "channels.json").write_text(json.dumps({
        "@old": "Old Format",
        "@new": {"name": "New Format", "notify": False, "sound": "none"},
    }), encoding="utf-8")
    storage.load_channels()
    assert storage.channels_snapshot() == {"@builtin": "Built-in", "@old": "Old Format", "@new": "New Format"}
    assert storage.get_options("@new") == {"notify": False, "upcoming": True, "sound": "none"}
    assert storage.get_options("@old") == storage.OPTION_DEFAULTS


def test_add_normalises_handle_and_rejects_duplicates(isolated):
    assert storage.add_channel("foo", "Foo")[0]
    assert "@foo" in storage.channels_snapshot()
    assert not storage.add_channel("@foo", "Foo again")[0]
    assert read(isolated / "channels.json") == {"@foo": "Foo"}


def test_cannot_remove_builtin(isolated):
    storage.load_channels()
    ok, msg = storage.remove_channel("@builtin")
    assert not ok and "built-in" in msg


def test_remove_clears_options_and_state(isolated):
    storage.add_channel("@foo", "Foo")
    storage.set_options("@foo", {"notify": False})
    storage.set_notified("@foo", "vid1")
    storage.set_notified("@foo#soon", "vid2")
    assert storage.remove_channel("@foo")[0]
    assert "@foo" not in storage.options
    assert storage.last_notified("@foo") is None and storage.last_notified("@foo#soon") is None


def test_default_options_are_not_persisted(isolated):
    storage.add_channel("@foo", "Foo")
    storage.set_options("@foo", {"notify": False})
    assert read(isolated / "channels.json")["@foo"] == {"name": "Foo", "notify": False}
    storage.set_options("@foo", dict(storage.OPTION_DEFAULTS))     # back to defaults
    assert read(isolated / "channels.json") == {"@foo": "Foo"}


def test_builtin_with_custom_options_is_persisted(isolated):
    storage.load_channels()
    storage.set_options("@builtin", {"sound": "none"})
    assert read(isolated / "channels.json") == {"@builtin": {"name": "Built-in", "sound": "none"}}
    storage.load_channels()
    assert storage.get_options("@builtin")["sound"] == "none"


def test_state_roundtrip(isolated):
    storage.set_notified("@foo", "vid")
    storage.state.clear()
    storage.load_state()
    assert storage.last_notified("@foo") == "vid"
    storage.set_notified("@foo", None)
    assert read(isolated / "state.json") == {}


def test_corrupt_json_falls_back_to_empty(isolated):
    (isolated / "channels.json").write_text("{not json", encoding="utf-8")
    storage.load_channels()
    assert storage.channels_snapshot() == {"@builtin": "Built-in"}


def test_settings_roundtrip_ignores_unknown_keys(isolated):
    settings.update(check_interval=120, bogus=1)
    (isolated / "settings.json").write_text(
        json.dumps({"check_interval": 30, "bogus": 1}), encoding="utf-8")
    settings.load()
    assert settings.get("check_interval") == 30
    assert "bogus" not in settings.all_values()
    settings.update(check_interval=settings.DEFAULTS["check_interval"])
