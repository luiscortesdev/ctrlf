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

The same things one piece at a time, for scripts and other clients (the dashboard uses the
ones above):

| Method | Path | What it does |
|---|---|---|
| GET | `/api/status` | `status` from `/api/state` (`state`, `target`, `result`, `message`) plus `target_bbox`, `aim`, `teach`, `voice`, `detector_fps`, `laser_connected`, `now` |
| GET | `/api/items` | `{"items": [...]}`: `items` from `/api/state`, each with `snapshot_url` (or `null`) |
| GET | `/api/items/<name>/snapshot` | That thing's last-seen snapshot (the whole view, its box drawn on). 404 if there's none |
| POST | `/api/query` | `{"query": "where are my keys?"}`: same as `/api/command`. Replies `{"ok": true, "action": "query", "query": ...}`; the answer shows up in `/api/status` |
| POST | `/api/teach` | `{"label": "keys"}`: start teaching it, like saying "learn my keys" (up to 40 letters, numbers, spaces) |
| POST | `/api/point` | `{"target": "keys"}`: point the laser at it (where it is, or where it was last seen), without answering out loud. `{"x": 0-1, "y": 0-1}`: point at that spot in the room view |
| GET | `/api/video_feed` | Same as `/video` |

`/api/status`, `/api/items` and the snapshot route answer 503 for the second or so before
the app's first pass. Bad requests get 400 with `{"ok": false, "error": "..."}`. Every
`POST` is queued for the main loop, so it replies straight away; watch `/api/status` for
what happened. Tests: `pytest tests/test_web_api.py`.

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
4. Add a test in `tests/test_web_api.py`: it runs the real server on a free port with a
   stand-in app.
