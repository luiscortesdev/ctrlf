# wherewolf: handoff for Claude Code

Everything from the planning/UI chat, so a new session can pick up where it stopped.
Paste this file to Claude Code (or keep it in the repo root; it's untracked, don't commit it).

## The project

- **Event:** SwampHacks XII at the University of Florida. Goal: win.
- **Team:**
  - Andrew: website/frontend.
  - Luis: backend. Repo: `github.com/luiscortesdev/ctrlf`.
  - Daniel: hardware/computer vision, Mac app.
- **Product:** **wherewolf** (formerly "Ctrl+F for Your Room"), slogan "Ctrl+F for your room".
  - A camera remembers where your things are.
  - Ask "where are my keys?" (typed or out loud) and a red laser on a pan/tilt ESP32 rig points at them.
  - Items are "key items": only things the user teaches it (through the laptop webcam), starting from none.
- **Logo:** Andrew's wolf head (one dark eye, one red eye) + "wherewolf." wordmark, as inline SVG in `web/js/brand.js`.
  - Colors: bone `#BAB8B3`, ash `#6E6A72`, ember `#A94B40`, ink `#1A191C`.
- **Hardware:**
  - 3D-printed wolf head (being printed in the lab): the laser sits behind the red eye, and the Tapo Wi-Fi camera is fixed in the left eye.
  - The laser reaches about ±25°. Mount the head 2–3 m away and up high.
  - Use the app's D key to set the desk area.
  - Still to do:
    - The LED ring states (looking / found / out of reach) need the ESP32 pin for the LED ring's data wire. Not given yet.
    - Optional: out-of-reach handling and a laser-reach overlay.
  - CAD files are in the untracked `case/` folder.

## How Andrew wants to work

- Be direct. Say when something is off track.
- Quality and integrity matter: sleek, modern, minimal, works for real.
- **Before major UI changes, list the changes first.** He approved the current redesign ("I like EVERY idea… implement that now").
- He'll answer questions for specifics. Ask when a choice really changes the result.

## Repo and git

- Local repo: `C:\Users\andre\OneDrive\Desktop\Projects\SwampHacks\ctrlf`, branch **`wherewolf-ui`**.
- Pushed so far: `6f96467` (forest world), merged with `main` at `603612f`.
  - In that merge, Luis's `main` backend won (his Windows support from PR #13, `src/device.py`, `src/tts.py`).
  - Andrew's old `src/machine.py` was deleted.
- **Never `git add .`**: untracked junk (`case/`, `ctrl-f-web.zip`, `head.py`, `led_ring_holder.stl`, `ctrlf_case_v2_*.png`) would get committed.
- Files show as modified when only their line endings changed (CRLF). Ignore those, e.g. `src/web.py`, `web/API.md`, `web/classic.html`.
- Pull request: open https://github.com/luiscortesdev/ctrlf/compare/main...wherewolf-ui. Don't push local `main`.
- Run the site:
  - With the real app: `python scripts/ctrlf.py`, then open http://localhost:8000. The page shows "Connected".
  - Website only: `python -m http.server 8000` inside `web/`. The page shows "Demo mode".
  - `dashboard.html?mock` forces demo data.

## The website (`web/`): plain HTML/CSS/JS, no build step

- `index.html` + `js/intro.js`: an ~18 s three.js/GSAP intro. It ends by diving into the red period of the logo, sets `sessionStorage.ctrlf-arrive=1`, and goes to `dashboard.html`. **Keep it.**
- `dashboard.html`: the app page. **Being rebuilt now** (see below).
- `classic.html` + `js/dashboard.js`: the older dashboard, linked as "Classic view".
- `js/api.js`: the only file that talks to the backend. Mock mode works when there's no app.
  - Endpoints: SSE `/api/events` (falls back to polling `/api/state`), `/api/command`, `/api/click`, `/api/listen`, `/video`, `/video/teach`, `/snapshots/`, `/taught/<name_with_underscores>.jpg`.
  - State: `items[{name, visible, last_seen, where, snapshot, bbox}]`, `taught[]`, `status{state: IDLE|QUERYING|TRACKING|REPORTING|TEACHING, result: found|hidden|info, message}`, `target`, `aim`, `voice`, `teach{phase, name, laptop_feed}`.
  - Teach phases: `first name`, `naming`, `looking`, `learning`, `placing`, `confirm`, `click`.
  - Commands: `this is my <name>`, `yes`, `no`, `cancel`, `forget <name>` (removes a key item, its photo and its memory).
- `js/sound.js`: recorded CC0 clips in `web/audio/` from UI SFX (uisfx.com), with a "Sci-fi" pack and a "Cinematic" pack.
  - Exports: `hover press tick toggle swoosh whoosh sweep laserCharge laserSnap shing added remove chime lowTone decode hum`.
  - The app page only uses found / added / removed / not found.
- `js/fx.js`: the laser cursor, effects and text animations.
  - `init({ grid, cursor, sounds })`. The app page uses all three set to false: normal cursor, no hover/click sounds.
- `js/brand.js`: `mount()` fills in `data-brand="mark|wordmark|lockup|icon"` elements.

### History of the dashboard (what was tried, so it isn't repeated)

1. **3D forest world with a guide wolf.** The wolf stood at a fork with two paths: "The Hunt" (add) and "The Pack" (items). The camera flew down a path and up into the sky to show panels. Pushed as `6f96467`.
2. **Andrew's next change list:**
   - Drop the wolf and its howl.
   - Swap every synthesized sound for real, cool, unique sounds.
   - Rename to "Add a key item" and "Your key items" with bigger labels.
   - Add deleting items.
   - Use a font that matches the logo (Inter Tight).

   Done and delivered, but **not yet committed** on his machine.
3. **Current, approved redesign:** make it professional, simple and accessible for older, less tech-savvy people while keeping the theme.
   - The 3D stays only as a slowly drifting background.
   - No 3D navigation or camera flights.
   - New professional font: **Atkinson Hyperlegible Next**, from the Braille Institute, built for readability (replaces Inter Tight).

### The current redesign (files are on Andrew's computer now, not committed)

- `dashboard.html`: one page with three views that fade in place (no camera flights):
  - **Home:**
    - Big "What are you looking for?".
    - A large search box, a red **Find** button and **Ask out loud** (push-to-talk; hidden when the app's voice is off).
    - Typing just "keys" becomes "where is my keys".
    - **Your key items:** big photo cards showing name, where, "Seen N minutes ago", and a text status ("In view now" / "Out of sight" / "Not seen yet").
    - Each card has two buttons, **Find** and **Remove**. Remove confirms first: "Yes, remove" / "Cancel".
    - **+ Add a key item** button. When empty: a "No key items yet" panel with a big "Add your first key item" button.
  - **Add a key item:**
    - A Back button and a numbered stepper (1 Name it, 2 Show it, 3 Place it).
    - Step headings "Step 2 of 3: …", big Yes/No buttons, then "Done! X is now a key item."
  - **Find (no camera picture; the camera only shows while adding an item):**
    - A Back button, then a laser status with a red orb and a status line: "Initializing laser…" → "Laser ready. Looking…" → "Pointing the laser…" → "Laser is on it" → "Laser finished" (or "Laser standing by" when out of sight / not found).
    - A big headline ("Found your keys" / "Your keys are out of sight" / "Not found") and the answer as one sentence.
    - The item's details: its photo with a status badge (In view now / Out of sight / Not seen yet), name, Where, Last seen (clock time + "just now"), Right now (in view of the camera or not), and Laser (what the laser is doing).
    - A checklist (Heard you / Laser ready / Found it / Pointing at it) and a big Done button.
    - In demo mode the browser speaks the answer. In live mode the app's own speaker does.
    - Tested in demo mode and against the fake app (add → find → remove), no errors.
  - Header: logo, connection status ("Connected" / "Demo mode"), "Sound: On/Off". Footer: "Replay intro", "Classic view".
- `css/app.css` (new; replaces `css/world.css`, which should be deleted):
  - Base text 18px, buttons at least 52–64px with words (no icon-only or hover-only controls).
  - Strong contrast (text 11:1, small text at least 7:1), visible focus rings.
  - Reduced motion respected. Phone layout at 720px and under.
- `css/base.css`: fonts are now Atkinson Hyperlegible Next (`vendor/fonts/atkinson-hyperlegible-next*.woff2`).
- `js/world/main.js`: rewritten for the views above.
  - Only re-renders the cards when what they show changes (live updates come several times a second and made cards flicker).
- `js/world/add.js`: the teaching flow.
  - Live: follows the app's teach phases and sends yes/no/clicks.
  - Demo: browser webcam photos.
  - Copy simplified, sounds removed except "added".
- `js/world/forest.js`: the scene, with a red beacon where the wolf was.
  - `drift(true)` makes it a slowly moving background. The intro's red shrinks into the beacon.
  - The moon moved to the right so it isn't behind the logo.

### Tested so far

- Demo mode (Playwright): empty state → add an item through all steps → card appears → Remove confirm → Cancel → Find "keys" → find screen with the laser box and the answer → Remove → empty again. No errors in the browser console.
- Phone width looks right.
- The previous version was also tested against a fake app that uses the real `src/web.py` (SSE): teaching, finding and deleting all worked.

### Left to do

1. **Test the new page live** with the real app (`python scripts/ctrlf.py`): add an item, find it, remove it.
2. Update `web/README.md`:
   - The dashboard section should describe the single home page (not the forest paths).
   - Font is Atkinson Hyperlegible Next.
   - `css/app.css` replaces `css/world.css`.
   - `js/world/add.js` replaces `hunt.js`.
3. Commit (PowerShell, from the `ctrlf` folder):
   ```
   git rm web/js/world/hunt.js web/css/world.css web/vendor/fonts/hubot-sans.woff2 web/vendor/fonts/mona-sans.woff2 web/vendor/fonts/monaspace-krypton-400.woff2 web/vendor/fonts/monaspace-krypton-500.woff2 web/vendor/fonts/OFL.txt
   del web\vendor\fonts\inter-tight*
   git add web/dashboard.html web/README.md web/css web/js web/audio web/vendor/fonts
   git commit -m "Simple, accessible app page: one home screen, Atkinson Hyperlegible, recorded sounds, remove items"
   git push
   ```
4. Optional:
   - Hardware LED ring states (needs the ESP32 pin).
   - Out-of-reach message when the laser can't point that far.
   - Ask Luis about a lighter detector setting if the demo laptop has no GPU (his `main` runs full settings on CPU, about 1 s per pass on a Core Ultra).
