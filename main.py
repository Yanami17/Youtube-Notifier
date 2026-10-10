from notifier import storage
from notifier.tray import run_tray

if __name__ == "__main__":
    storage.load_channels()
    storage.load_state()
    run_tray()
