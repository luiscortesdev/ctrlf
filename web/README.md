# wherewolf: website

*Ctrl+F for your room.*

Three pages, plain HTML/CSS/JS, no build step. Every library and font is in `vendor/`, so it works with no internet.

- `index.html`: the ~18 second 3D intro (three.js + GSAP). Plays once, then goes to the dashboard.
- `dashboard.html`: **the app.** One page, built to be simple for anyone (big text, big buttons with words, strong contrast). The night forest (three.js) only drifts slowly in the background. Three views fade in place:
  - **Home**: "What are you looking for?" with a large search box, a red **Find** button and **Ask out loud** (push-to-talk, hidden when the app's voice is off). Typing just "keys" asks "where is my keys". Below it, **Your key items**: a photo card for each thing you taught, with where it is, "Seen N minutes ago" and a status in words ("In view now" / "Out of sight" / "Not seen yet"). Each card has **Find** and **Remove** (Remove asks first). **+ Add a key item** starts teaching; with nothing taught yet there's one big "Add your first key item" button.
  - **Add a key item**: a 3-step guide (1 Name it, 2 Show it, 3 Place it) with big Yes/No buttons, ending in "Done! X is now a key item."
  - **Find**: a big headline ("Found your keys" / "Your keys are out of sight" / "Not found"), the answer as one sentence, the room camera with the laser drawn on the item, a checklist (Heard you / Found it / Pointing / Done), the last-seen photo when it's out of sight, and a big **Done** button. In demo mode the browser speaks the answer; with the app running, the app's own speaker does.
  - Header: logo, connection ("Connected" / "Demo mode"), "Sound: On/Off". Footer: "Replay intro", "Classic view".
- `classic.html`: the previous dashboard (live feed, status, last seen, activity). Linked as "Classic view" in the footer.

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

Sounds are recorded clips in `audio/` from [UI SFX](https://uisfx.com) (CC0, public domain: `audio/LICENSE.txt`): the "Sci-fi" pack and the "Cinematic" pack. The intro uses many of them; the app page only plays four: found, added, removed and not found. `js/sound.js` has one function per sound; to change one, replace its file in `audio/`.

## Look and feel

- **Brand** (`js/brand.js`): the wolf mark, app icon and "wherewolf." wordmark as inline SVG, traced from the logo. Put `<span data-brand="mark|wordmark|lockup|icon"></span>` anywhere and it's filled in. Favicon: `img/favicon.svg`.
- **Type** (`vendor/fonts`, SIL OFL): Atkinson Hyperlegible Next everywhere, by the Braille Institute, made so similar letters (1 I l, 0 O) are easy to tell apart. Easy to read for older eyes.
- **Accessibility** (`css/app.css`): 18px base text, buttons at least 52–64px tall with words (no icon-only or hover-only controls), text contrast 11:1 (small text at least 7:1), visible focus rings, "Reduce motion" respected, phone layout at 720px and under.
- **Effects** (`js/fx.js`): the laser-dot cursor and its spark trail, the dot-grid background that bends toward the cursor (plus a slow scan line and ripples when something is found), hover/press sounds, text scramble, dialogs that grow out of the button that opened them, and the logo's eyes following the cursor. To turn one off, delete its line in `init()` at the bottom of the file. "Reduce motion" in the OS turns the motion off automatically. These are for the intro and the classic view; the app page turns them all off (`fx.init({ grid: false, cursor: false, sounds: false })` in `js/world/main.js`) for a normal cursor and no hover/click sounds.
- **Hover styles** (classes in `css/base.css`): `fx-wipe` (laser wipe + scramble, primary buttons), `fx-brackets` (corner brackets lock on, secondary buttons and chips), `fx-ripple` (the mic), `fx-spot` (red spotlight border that follows the cursor, cards and rows).

## Handy URLs

| URL | What it does |
|---|---|
| `/index.html?replay` | Play the intro again |
| `/index.html?replay&at=12.5` | Freeze the intro at 12.5 seconds (for tweaking) |
| `/dashboard.html?mock` | Force demo data even when the backend is up (taught items are remembered in this browser) |
| `/classic.html` | The previous dashboard |

## Where to change things

| Change | File |
|---|---|
| Intro timing, text lines, camera moves | `js/intro.js`: the `buildTimeline()` section. Times are in seconds. |
| 3D objects and lighting | `js/intro.js`: top "Scene" section |
| Colors, fonts | `css/base.css`: the `:root` variables |
| Logo | `js/brand.js` |
| Cursor, background, hover effects | `js/fx.js` + the `fx-*` classes in `css/base.css` |
| Background forest: trees, beacon, moon, slow drift | `js/world/forest.js` (`drift(true)` turns on the slow background motion) |
| App page: home, search, key item cards, find view | `dashboard.html` + `css/app.css` + `js/world/main.js` |
| Adding a key item (teaching steps) | `js/world/add.js` |
| Classic dashboard layout | `classic.html` + `css/dashboard.css` |
| Sounds | `js/sound.js`: one function per sound |
| Backend connection | `js/api.js` (the only file that talks to the app) and `src/web.py` in the repo |
| Classic teach mode | `js/teach.js` |
| Arrival animation after the intro (the red shrinks into the beacon) | `arrive()` in `js/world/main.js` |
