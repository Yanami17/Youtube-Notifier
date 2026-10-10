# YouTube Live Notifier

A Windows tray app that watches YouTube channels and pops up a toast when one goes live
(or is about to). No API key needed — it reads the public channel pages.

## Run it

**Easiest — the launcher:** double-click `YouTubeNotifier.exe`.
On every launch it checks whether the sources are newer than `bin\YouTubeNotifierApp.exe`;
if so it rebuilds the app with PyInstaller (~30 s, silent) and then starts it.
A failed build falls back to the previous build, with details in `build.log`.
Rebuilding needs Python on `PATH` with `pip install -r requirements.txt`.

**From source:**
```
pip install -r requirements.txt
python main.py        # pythonw main.py for no console window
```

Only one copy runs at a time. The app lives in the system tray (check the `^` overflow).

## Tray menu

| Item | What it does |
|---|---|
| Live channels | Click to open the stream |
| Refresh now | Check immediately and re-show toasts for anything live |
| Pause notifications | Snooze for 30 min – 4 h. Streams that start meanwhile notify on resume |
| Add / Manage / Remove channel | Add by handle or pasted URL. *Manage* edits per-channel notifications |
| Settings… | Check interval, toast duration, sound, starting-soon alerts, start with Windows |
| Open log | Opens `notifier.log` |

Toasts: click the card (or *Watch now*) to open the stream, *Dismiss* to close it. The countdown bar
pauses while the mouse is over the toast.

Per channel (Manage channels → select → *Notification settings…*): mute, starting-soon alerts on/off,
and a sound (default, silent, or any `.wav`).

## Files

Everything lives next to the exe / project root and is git-ignored:

| File | Contents |
|---|---|
| `channels.json` | Monitored channels, `"@handle": "Name"` or `{"name": ..., "notify": false, "sound": "none"}` |
| `settings.json` | Settings from the Settings dialog |
| `state.json` | Last notified stream per channel (cleared on Exit) |
| `notifier.log` | Rotating log (the windowed exe has no console) |

Built-in channels can be hard-coded in `notifier/config.py` (`CHANNELS`).

## Layout

```
main.py              entry point: logging, single-instance, load data, run tray
launcher.py          builds to YouTubeNotifier.exe; rebuilds the app when sources change
app.spec / launcher.spec   PyInstaller specs
notifier/
  config.py          constants and data paths
  settings.py        settings.json
  storage.py         channels, per-channel options, notified-state
  youtube.py         page scraping (live + upcoming), CheckError
  monitor.py         polling loop, notification rules, pause
  tray.py            tray icon, animation, menu
  ui.py              Tk toasts and dialogs (single UI thread)
  icons.py           icon artwork; `python -m notifier.icons` regenerates assets/icon.ico
  log.py instance.py startup.py
tests/               pytest suite
```

## Tests

```
pip install -r requirements-dev.txt
python -m pytest
```

## Troubleshooting

- **Nothing notifies / tooltip says "⚠ N channel(s) failing":** open the log. Usually a wrong handle
  (HTTP 404), a network problem, or YouTube changed its page markup (`unrecognised page`) —
  the markers live in `notifier/youtube.py` and are covered by `tests/test_youtube.py`.
- **"Already running" message:** it's in the tray; Exit it from there first.
- **Rebuild didn't happen:** read `build.log`; the launcher skips rebuilds while the app is running.
