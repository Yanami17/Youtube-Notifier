import requests
import signal
import sys
import time
import json
import os
import threading

from winotify import Notification, audio
from config import CHANNELS, CHECK_INTERVAL, APP_ID, STATE_FILE

import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw


running = True
state = {}

# =========================
# Helpers
# =========================

def download_thumbnail(url, video_id):
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            filename = f"thumb_{video_id}.jpg"
            with open(filename, "wb") as f:
                f.write(response.content)
            return os.path.abspath(filename)
    except Exception as e:
        print("Thumbnail download error:", e)

    return None

# =========================
# State Handling
# =========================

def clear_live_state(state):
    save_state({})
    print("Cleared live state.")



def load_state():
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=4)


def check_live_status(handle):
    live_url = f"https://www.youtube.com/{handle}/live"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    try:
        response = requests.get(live_url, headers=headers, timeout=10)

        if response.status_code != 200:
            return None

        html = response.text

        # Only trigger if actually live
        if '"isLiveNow":true' not in html:
            return None

        marker = '"canonicalUrl":"https://www.youtube.com/watch?v='

        if marker in html:
            start = html.index(marker) + len(marker)
            video_id = html[start:start+11]

            return {
                "video_id": video_id,
                "title": f"{handle} is Live!",
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "thumbnail": f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"
            }

    except Exception as e:
        print("Live check error:", e)

    return None


def show_notification(channel_name, stream):

    image_path = None

    if stream.get("thumbnail"):
        image_path = download_thumbnail(stream["thumbnail"], stream["video_id"])

    toast = Notification(
        app_id=APP_ID,
        title=f"{channel_name} is LIVE",
        msg=stream["title"],
        icon=image_path,
        duration="long"
    )

    toast.set_audio(audio.Default, loop=False)
    toast.add_actions(label="Watch Stream", launch=stream["url"])
    toast.show()

    # Allow Windows time to load the image before deleting
    if image_path and os.path.exists(image_path):
        time.sleep(2)
        try:
            os.remove(image_path)
        except Exception as e:
            print("Thumbnail cleanup error:", e)


# =========================
# Background Loop
# =========================

def checker_loop():
    global state
    global running

    print("YouTube Live Notifier started.")
    print("State file path:", os.path.abspath(STATE_FILE))

    state = load_state()

    while running:
        try:
            for handle, channel_name in CHANNELS.items():

                stream = check_live_status(handle)

                if stream is None:
                    if state.get(handle) is not None:
                        state[handle] = None
                        save_state(state)
                    continue


                last_video_id = state.get(handle)

                if last_video_id != stream["video_id"]:
                    print(f"{channel_name} went live!")
                    show_notification(channel_name, stream)

                    state[handle] = stream["video_id"]
                    save_state(state)

        except Exception as e:
            print("Error:", e)

        time.sleep(CHECK_INTERVAL)


# =========================
# Tray Icon
# =========================

def create_image():
    width = 64
    height = 64
    image = Image.new("RGB", (width, height), "red")
    dc = ImageDraw.Draw(image)
    dc.rectangle((16, 16, 48, 48), fill="white")
    return image


def quit_app(icon, item):
    global running
    print("Shutting down notifier...")
    running = False
    clear_live_state(state)
    icon.stop()


def run_tray():
    icon = pystray.Icon(
        "YouTubeNotifier",
        create_image(),
        "YouTube Live Notifier",
        menu=pystray.Menu(
            item("Exit", quit_app)
        )
    )

    icon.run()


# =========================
# Entry
# =========================

if __name__ == "__main__":

    thread = threading.Thread(target=checker_loop, daemon=True)
    thread.start()

    run_tray()
