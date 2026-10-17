"""
Tells your phone what you forgot when you leave home, for Ctrl+F for Your Room.

The camera can't tell leaving the room from leaving home, but your phone can: an iPhone
Shortcuts automation ("when I leave home") opens a web address, and the app hears it.
It then looks for the essentials (src/app.py's ESSENTIALS) for a few seconds: whatever is
still sitting in the room, you forgot, and it pushes that to your phone.

Both directions go through ntfy (ntfy.sh), a free push-notification service: no app to
build, no server to run, no account. Only a one-line text leaves the Mac, never a picture.
  <topic>       the app sends alerts here; the ntfy app on your phone subscribes to it
  <topic>-left  the phone's Shortcut opens <server>/<topic>-left/trigger when you leave
Type "phone" in the app for the setup steps, "phone test" to send a test alert, and
"left home" to pretend the phone said you left.

Anyone who knows a topic's name can read it and post to it, so the name is random
(saved in data/phone.json). Fine for a demo; for real use, a self-hosted ntfy with access tokens.
"""

import json
import os
import secrets
import threading
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the repo
CONFIG_PATH = os.path.join(ROOT, "data", "phone.json")  # {"server": ..., "topic": ...}
SERVER = "https://ntfy.sh"
MAX_AGE = 300  # a "left home" older than this (sent while the app was off) is ignored


class Phone:
    def __init__(self, on_left):
        """on_left(): called from the listening thread when the phone says you left."""
        self.on_left = on_left
        self.server, self.topic = SERVER, None
        try:
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
            self.server, self.topic = cfg.get("server", SERVER).rstrip("/"), cfg["topic"]
        except (OSError, ValueError, KeyError):
            self.topic = "ctrlf-" + secrets.token_hex(4)
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                json.dump({"server": self.server, "topic": self.topic}, f, indent=1)
        self.connected = False
        threading.Thread(target=self._listen, daemon=True).start()

    @property
    def trigger_url(self):
        return f"{self.server}/{self.topic}-left/trigger"

    def send(self, title, message, priority=4, tags=("warning",)):
        """Push a notification (in the background, so the video never waits).
        priority: ntfy's 1-5; 4 = high, 2 = low (no sound or vibration)."""
        def post():
            try:
                requests.post(self.server + "/", timeout=10, json={
                    "topic": self.topic, "title": title, "message": message,
                    "priority": priority, "tags": list(tags)}).raise_for_status()
            except requests.RequestException as e:
                print(f"\n(phone: couldn't send the alert: {e})")
        threading.Thread(target=post, daemon=True).start()

    def setup_steps(self):
        return f"""
Phone alerts, through ntfy ({self.server}). {'Listening' if self.connected else 'Not connected yet'}.
  1. On the phone, install "ntfy" from the App Store, tap +, and subscribe to:
         {self.topic}
     Allow notifications.
  2. Shortcuts app > Automation > + > Leave > choose Home (or wherever this runs),
     Run Immediately. Add the action "Get Contents of URL" with:
         {self.trigger_url}
  3. For the demo, make the same action a normal Shortcut called "I'm heading out",
     so a tap (or "Hey Siri, I'm heading out") does it.
Test: "phone test" sends an alert; "left home" acts as if the phone said you left."""

    def _listen(self):
        """Stay subscribed to <topic>-left; reconnect when the connection drops."""
        url = f"{self.server}/{self.topic}-left/json"
        last_id, failed = None, False
        while True:
            try:
                params = {"since": last_id} if last_id else {}  # catch what came while reconnecting
                # ntfy sends a keepalive every 45 s, so 90 s of silence = a dead connection.
                with requests.get(url, params=params, stream=True, timeout=(10, 90)) as r:
                    r.raise_for_status()
                    self.connected, failed = True, False
                    for line in r.iter_lines():
                        if not line:
                            continue
                        msg = json.loads(line)
                        if msg.get("event") != "message":
                            continue
                        last_id = msg["id"]
                        if time.time() - msg.get("time", 0) <= MAX_AGE:
                            self.on_left()
            except (requests.RequestException, ValueError) as e:
                if not failed:  # say it once, not every retry
                    print(f"\n(phone: can't reach {self.server}, retrying: {e.__class__.__name__})")
                failed = True
            self.connected = False
            time.sleep(5)
