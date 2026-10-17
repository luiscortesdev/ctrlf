# How the website talks to the app

The website is served by the app itself (`src/web.py`), so it's on the same address as the
API: run `python scripts/ctrlf.py` and open http://localhost:8000. Only the standard
library is used, so there's nothing extra to install.

In the browser, `js/api.js` is the only file that talks to the app. If the app isn't
running (or the URL has `?mock`), the dashboard runs on demo data instead.

## Endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/api/state` | Everything the dashboard shows (below). Polled about 4 times a second. |
| POST | `/api/command` | `{"text": "..."}`: exactly like typing in the app's Terminal: `where are my keys?`, `learn this`, `this is my wallet`, `yes`, `no`, `cancel`, a name, `forget keys`, `wipe`, `list` |
| POST | `/api/listen` | Push-to-talk, like SPACE in the video window |
| POST | `/api/click` | `{"x": 0-1, "y": 0-1}`: while teaching, "it's this one" (like clicking the video window) |
| GET | `/video` | Live room view with the tracked boxes (MJPEG; `<img src="/video">`) |
| GET | `/video/teach` | The laptop camera while teaching with it (MJPEG) |
| GET | `/snapshots/<file>` | Last-seen snapshots |
| GET | `/taught/<file>` | Photos of taught things |

## `/api/state`

```json
{
  "items": [
    {"name": "keys", "visible": true, "last_seen": "2026-10-17T14:15:02",
     "where": "on the chair", "snapshot": "/snapshots/keys_1760...jpg",
     "bbox": [0.16, 0.58, 0.23, 0.69]}
  ],
  "can_find": ["airpods case", "keys", "wallet"],
  "taught": ["retainer case"],
  "status": {"state": "REPORTING", "target": "keys", "result": "found",
             "message": "Your keys are on the chair."},
  "target": {"name": "keys", "bbox": [0.16, 0.58, 0.23, 0.69]},
  "aim": "hold",
  "teach": null,
  "voice": "idle",
  "heard": {"time": "...", "text": "where are my keys"},
  "said": [{"time": "...", "text": "Your keys are on the chair."}],
  "detector_fps": 2.4,
  "laser_connected": true
}
```

- `status.state`: `IDLE`, `QUERYING` (just asked), `TRACKING` (laser moving onto it),
  `REPORTING` (`result`: `found`, `hidden` = out of sight, or `info` = any other answer),
  `TEACHING`.
- `aim`: the laser aimer's state: `idle`, `off` (head moving), `on` (correcting onto the
  dot), `hold` (on target).
- `teach`: while teaching, `{"phase", "name", "laptop_feed"}`. Phases come from
  `App.teach` in `src/app.py`: `first name`, `looking`, `naming`, `placing`, `confirm`,
  `learning`, `click`.
- `bbox`: fractions of the camera image `[x1, y1, x2, y2]`. Furniture isn't listed.

## Adding something new

1. Put it in `WebBridge._state()` in `src/web.py` (built in the app's main loop, so it's
   safe to use `app.mem`, the tracker, and so on there, never in the request handlers).
2. Read it in `applyState()` in `js/api.js` and emit an event for the dashboard.
3. New actions: add a `POST` in `_Handler.do_POST`, queue it on `bridge.actions`, and
   handle it in the `if web:` block of the main loop in `src/app.py`.
