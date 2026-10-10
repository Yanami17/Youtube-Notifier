"""Notification rules in monitor.run_single_check, with network, UI and disk stubbed out."""

import time

import pytest

from notifier import monitor, settings, storage
from notifier.youtube import ChannelStatus, CheckError

OPTS = {"notify": True, "upcoming": True, "sound": "default"}


def live(video_id="v1"):
    return {"video_id": video_id, "title": "T", "url": "u", "thumbnail": "t", "viewers": None, "start_time": None}


def upcoming(in_minutes, video_id="u1"):
    return {"video_id": video_id, "title": "T", "url": "u", "thumbnail": "t",
            "start_epoch": int(time.time() + in_minutes * 60)}


@pytest.fixture
def env(monkeypatch):
    toasts, results, opts = [], {}, {}
    monkeypatch.setattr(monitor, "show_live_toast", lambda **kw: toasts.append(kw))
    monkeypatch.setattr(monitor, "check_channel", lambda h: results[h]() if callable(results[h]) else results[h])
    monkeypatch.setattr(storage, "save_state", lambda: None)
    monkeypatch.setattr(storage, "get_options", lambda h: {**OPTS, **opts.get(h, {})})
    monkeypatch.setattr(storage, "channels_snapshot", lambda: {"@a": "A"})
    monkeypatch.setattr(settings, "_values", dict(settings.DEFAULTS))
    storage.state.clear()
    monitor._live_now.clear()
    monitor._errors.clear()
    monitor.resume()
    yield toasts, results, opts
    monitor.resume()


def test_notifies_once_per_stream(env):
    toasts, results, _ = env
    results["@a"] = ChannelStatus(live=live("v1"))
    monitor.run_single_check()
    monitor.run_single_check()
    assert len(toasts) == 1 and toasts[0]["channel_name"] == "A"
    results["@a"] = ChannelStatus(live=live("v2"))     # a different stream notifies again
    monitor.run_single_check()
    assert len(toasts) == 2


def test_force_notify_repeats(env):
    toasts, results, _ = env
    results["@a"] = ChannelStatus(live=live())
    monitor.run_single_check()
    monitor.run_single_check(force_notify=True)
    assert len(toasts) == 2


def test_going_offline_resets_so_next_stream_notifies(env):
    toasts, results, _ = env
    results["@a"] = ChannelStatus(live=live("v1"))
    monitor.run_single_check()
    results["@a"] = ChannelStatus()
    monitor.run_single_check()
    assert "@a" not in monitor.live_snapshot()
    results["@a"] = ChannelStatus(live=live("v1"))
    monitor.run_single_check()
    assert len(toasts) == 2


def test_check_error_keeps_state_and_is_reported(env):
    toasts, results, _ = env
    results["@a"] = ChannelStatus(live=live())
    monitor.run_single_check()

    def boom():
        raise CheckError("HTTP 500")
    results["@a"] = boom
    monitor.run_single_check()
    assert monitor.error_snapshot() == {"@a": "HTTP 500"}
    assert "@a" in monitor.live_snapshot()             # still considered live
    results["@a"] = ChannelStatus(live=live())
    monitor.run_single_check()
    assert monitor.error_snapshot() == {}
    assert len(toasts) == 1                            # the error did not cause a re-notification


def test_muted_channel_is_silent_and_stays_silent_when_unmuted(env):
    toasts, results, opts = env
    opts["@a"] = {"notify": False}
    results["@a"] = ChannelStatus(live=live())
    monitor.run_single_check()
    assert toasts == [] and "@a" in monitor.live_snapshot()
    opts["@a"] = {"notify": True}
    monitor.run_single_check()
    assert toasts == []


def test_pause_defers_notification_until_resume(env):
    toasts, results, _ = env
    monitor.pause(3600)
    results["@a"] = ChannelStatus(live=live())
    monitor.run_single_check()
    assert toasts == []
    monitor.resume()
    monitor.run_single_check()
    assert len(toasts) == 1


def test_upcoming_notifies_within_lead_time_only_once(env):
    toasts, results, _ = env
    results["@a"] = ChannelStatus(upcoming=upcoming(60))
    monitor.run_single_check()
    assert toasts == []                                # 60 min out > 15 min lead
    results["@a"] = ChannelStatus(upcoming=upcoming(10))
    monitor.run_single_check()
    monitor.run_single_check()
    assert len(toasts) == 1 and toasts[0]["badge"] == "SOON"


def test_upcoming_respects_global_and_channel_switches(env):
    toasts, results, opts = env
    results["@a"] = ChannelStatus(upcoming=upcoming(5))
    settings.update(notify_upcoming=False)
    monitor.run_single_check()
    assert toasts == []
    settings.update(notify_upcoming=True)
    opts["@a"] = {"upcoming": False}
    monitor.run_single_check()
    assert toasts == []
    opts["@a"] = {}
    monitor.run_single_check()
    assert len(toasts) == 1
