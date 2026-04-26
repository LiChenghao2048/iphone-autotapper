# Brawl Stars iPhone Bot

## Problem Statement

Automate playing Brawl Stars on a physical iPhone using WebDriverAgent (WDA) for input control and OpenCV for image-based game state detection. The bot screenshots the screen at a regular interval, evaluates game state (health bars, enemy positions, button cooldowns), then executes taps and swipes in response. All WDA operations are sequential — one action at a time.

**Tech stack:** WDA + iproxy (no jailbreak) · OpenCV · Python 3

---

## Tasks

### Phase 1 — Gesture layer
- [x] **Task 1** · `src/gestures/tap.py` — tap primitive with session retry, pause/resume, multi-coord CLI
- [x] **Task 2** · `src/gestures/swipe.py` — swipe primitive (A→B, configurable duration); refactor tap into `src/gestures/` subdirectory

### Phase 2 — Screenshot loop
- [ ] **Task 3** · `src/screenshot.py` — extract `take_screenshot()` from `pick_coords.py`; add `capture_loop(interval_ms, callback)` for headless use; CLI `--save screen.png`

### Phase 3 — Vision layer
- [ ] **Task 4** · `src/vision.py` — `find_template(img, template_path, threshold)` returning match locations; tests against fixture images
- [ ] **Task 5** · `src/vision.py` — `find_by_color(img, hsv_lower, hsv_upper)` and `read_bar_percent(img, region, hsv_range)` for enemy health bar detection and own HP% reading

### Phase 4 — Bot loop
- [ ] **Task 6** · `src/bot.py` — main screenshot → vision → act loop; configurable interval; dry-run mode (logs detections, no taps); state machine: `in_match / in_menu / game_over`
- [ ] **Task 7** · `src/presets/brawl_stars.py` — named action presets (attack, super, move); wired into bot loop; bot attacks when cooldown ready, retreats below HP threshold

### Phase 5 — Enemy targeting
- [ ] **Task 8** · enemy health bar color detection → compute direction from player centre → swipe toward nearest detected enemy
