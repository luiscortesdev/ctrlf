# Ctrl+F for Your Room: website

Two pages, plain HTML/CSS/JS, no build step. Every library and font is in `vendor/`, so it works with no internet.

- `index.html`: the ~18 second 3D intro (three.js + GSAP). Plays once, then goes to the dashboard.
- `dashboard.html`: live feed, ask bar, status, last seen, tracking, teaching, activity.

## Run it

**With the real app (camera, memory, laser):** from the repo folder, start the app as usual:

```
python scripts/ctrlf.py
```

It serves this website too: open http://localhost:8000. The dashboard shows "Connected" and everything is live. How they talk: `API.md`.

**Website only (designing, no camera):** from this folder:

```
python -m http.server 8000
```

With no app running, the dashboard uses **demo data** and your webcam as a stand-in feed. Stop this server before starting the app; they both use port 8000.

It must be served by a web server; double-clicking the HTML file won't work.

## Sound with no click (demo laptop)

Launch Chrome with autoplay allowed so the intro plays with sound immediately:

```
"C:\Program Files\Google\Chrome\Application\chrome.exe" --autoplay-policy=no-user-gesture-required --start-fullscreen http://localhost:8000
```

Without the flag, the intro shows a "Begin" button first (one click unlocks audio).

All sounds are generated in code (`js/sound.js`), so there are no audio files to download or license.

## Handy URLs

| URL | What it does |
|---|---|
| `/index.html?replay` | Play the intro again |
| `/index.html?replay&at=12.5` | Freeze the intro at 12.5 seconds (for tweaking) |
| `/dashboard.html?mock` | Force demo data even when the backend is up |

## Where to change things

| Change | File |
|---|---|
| Intro timing, text lines, camera moves | `js/intro.js`: the `buildTimeline()` section. Times are in seconds. |
| 3D objects and lighting | `js/intro.js`: top "Scene" section |
| Colors, fonts | `css/base.css`: the `:root` variables |
| Dashboard layout | `dashboard.html` + `css/dashboard.css` |
| Sounds | `js/sound.js`: one function per sound |
| Backend connection | `js/api.js` (the only file that talks to the app) and `src/web.py` in the repo |
| Teach mode (live: the app's own teaching; demo: browser webcam) | `js/teach.js` |
| Arrival animation after the intro | `arrive()` in `js/dashboard.js` |
