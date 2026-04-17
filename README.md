# iPhone Auto-Tapper

Repeatedly taps a fixed coordinate on a connected iPhone via WebDriverAgent (WDA) over USB. No jailbreak. Free Apple account is fine.

---

## Setup

1. Copy `.env.example` to `.env` and fill in your values:
   ```bash
   cp .env.example .env
   ```

   | Variable | How to find it |
   |---|---|
   | `UDID` | `xcrun devicectl list devices` |
   | `TEAM` | Xcode → Signing & Capabilities → Team ID |

2. Build and install WDA onto your device (one-time, see [7-day certificate renewal](#7-day-certificate-renewal) for the commands).

---

## Every-session startup

**Terminal 1 — coordinator (manages WDA automatically, keep open):**
```bash
cd ~/Claude_Project/iphone-autotapper
python3 coordinator.py
```
Wait for: `Ready — listening on http://localhost:9000`

**Terminal 2 — run a script:**
```bash
python3 control.py start script.yaml
```

**Terminal 3 (on demand) — control:**
```bash
python3 control.py pause
python3 control.py resume
python3 control.py status
python3 control.py stop
```

---

## Script format

Actions are defined in `script.yaml`. All actions run concurrently, each at its own interval.

```yaml
actions:
  - type: tap
    x: 701
    y: 402
    interval: 1.0   # seconds between taps
    count: 0        # 0 = run forever

  - type: drag
    x1: 100
    y1: 400
    x2: 100
    y2: 200
    duration: 500   # ms to complete the drag gesture
    interval: 3.0
    count: 10

  - type: circle
    center_x: 400
    center_y: 400
    radius: 100
    duration: 1000  # ms to complete one full circle
    interval: 0.5
    count: 0
```

### Action parameters

| Parameter | Applies to | Description |
|---|---|---|
| `x`, `y` | tap | Tap coordinate (logical points) |
| `x1`, `y1`, `x2`, `y2` | drag | Start and end coordinates |
| `center_x`, `center_y`, `radius` | circle | Circle geometry |
| `duration` | drag, circle | Gesture duration in ms |
| `interval` | all | Seconds between repetitions |
| `count` | all | Repetitions — 0 = forever |

---

## Finding coordinates

Phone must be on the right screen and orientation before screenshotting.

```bash
python3 find_coords.py    # screenshots current phone screen
python3 pick_coords.py    # opens browser picker → hover to read, click to confirm pixel (x, y)
```

Convert pixel → tap coords: divide both numbers by **3**.

**Portrait** pixel (px, py) → `--x $((px/3)) --y $((py/3))`

**Landscape** pixel (px, py) → same: `--x $((px/3)) --y $((py/3))`

Known working coordinates:
- Portrait center: `--x 215 --y 466`
- Landscape target (as of Apr 2026): `--x 701 --y 402`

---

## 7-day certificate renewal

Free accounts expire every 7 days. When `start_wda.sh` fails, rebuild WDA:

```bash
source .env
WDA_DIR=~/.appium/node_modules/appium-xcuitest-driver/node_modules/appium-webdriveragent

# 1. Build
xcodebuild -project "$WDA_DIR/WebDriverAgent.xcodeproj" \
  -scheme WebDriverAgentRunner \
  -destination "id=$UDID" \
  CODE_SIGN_STYLE=Automatic \
  CODE_SIGN_IDENTITY="Apple Development" \
  DEVELOPMENT_TEAM="$TEAM" \
  PRODUCT_BUNDLE_IDENTIFIER="com.$TEAM.WebDriverAgentRunner" \
  -configuration Debug \
  -allowProvisioningUpdates \
  build

# 2. Install
WDA_APP=$(ls -d ~/Library/Developer/Xcode/DerivedData/WebDriverAgent-*/Build/Products/Debug-iphoneos/WebDriverAgentRunner-Runner.app)
xcrun devicectl device install app --device "$UDID" "$WDA_APP"
```

No need to re-trust on the phone after renewal.

---

## Stack

```
tap.py ──HTTP──▶ WDA (port 8100)
                     │
                 iproxy (USB)
                     │
                 WDA runner on iPhone (XCUITest)
```

- `start_wda.sh` — launches `xcodebuild test-without-building` + `iproxy 8100 8100`
- `tap.py` — posts W3C pointer actions to WDA HTTP API
- `find_coords.py` + `pick_coords.py` — screenshot + browser coordinate picker
- WDA built from: `~/.appium/node_modules/appium-xcuitest-driver/node_modules/appium-webdriveragent/`
