# Brawl Stars iPhone Bot

## Problem Statement

Automate playing Brawl Stars on a physical iPhone using WebDriverAgent (WDA) for input control and OpenCV for image-based game state detection. The bot screenshots the screen at a regular interval, evaluates game state (health bars, enemy positions, button cooldowns), then executes taps and swipes in response. All WDA operations are sequential — one action at a time.

**Tech stack:** WDA + iproxy (no jailbreak) · OpenCV · Python 3

---

## Tasks

### Phase 1 — Gesture layer
- [x] **Task 1** · `src/gestures/tap.py` — tap primitive with session retry, pause/resume, multi-coord CLI
- [x] **Task 2** · `src/gestures/swipe.py` — swipe primitive (A→B, configurable duration); refactor tap into `src/gestures/` subdirectory
- [x] **Deferred (add before 3rd gesture file)** · extract shared `get_or_create_session()` and `WDA_URL` into `src/_session.py`; all WDA clients (`tap.py`, `swipe.py`, `pick_coords.py`) now import from there

### Phase 2 — Screenshot loop
- [x] **Task 3** · `src/screenshot.py` — extract `take_screenshot()` from `pick_coords.py`; add `capture_loop(interval_ms, callback)` for headless use; CLI `--save screen.png`

### Phase 3 — Sequence runner (POC validation)
- [x] **Task 4** · `src/sequence.py` — generic mixed-gesture sequence runner (`Tap`, `Swipe`, `Wait` steps); loads sequences from YAML preset files (`src/presets/<name>.yaml`); CLI `--preset NAME --count N`; pause/resume support; `src/presets/brawl_stars.yaml` stub included

### Phase 4+ — Cancelled

Phases 4 (Vision), 5 (Bot loop), and 6 (Enemy targeting) were scoped but not started. The project is stable and complete at Phase 3.

---

## Dev Tooling

### Coordinate Picker (`src/pick_coords.py`)
- [x] Initial picker — screenshot over HTTP, hover to preview logical-point coordinates, click to log `tap.py` command
- [x] Refactor — extract `take_screenshot()` into `src/screenshot.py`; import `WDA_URL` / `get_or_create_session()` from `src/_session.py`
- [x] Space-to-tap — press Space while hovering to fire a real WDA tap at the crosshair position; auto-refreshes screenshot on success; errors shown in log
- [x] Split X/Y inputs + Go — separate X and Y boxes replace the single `x,y` field; clicking the image fills both; Enter in either box submits
- [x] Copy button — copies `--x {tx} --y {ty}` to clipboard from the current crosshair position
- [x] Sidebar layout — image on the left, 320 px sidebar on the right (controls at top, scrollable click log below)

