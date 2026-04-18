# iPhone Auto-Tapper

Repeatedly taps a fixed coordinate on a connected iPhone via WebDriverAgent (WDA) over USB. No jailbreak. Free Apple account is fine.

---

## Setup

1. Copy `.env.example` to `.env` in the project root and fill in your values:
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

**Terminal 1 — start WDA (keep open):**
```bash
cd ~/Claude_Project/iphone-autotapper
python3 scripts/start_wda.py
```
Wait for: `WDA is live at http://127.0.0.1:8100`

**Terminal 2 — tap:**
```bash
python3 src/tap.py --x 701 --y 402            # tap forever at 1s interval
python3 src/tap.py --x 701 --y 402 --interval 0.5   # faster
python3 src/tap.py --x 701 --y 402 --count 60       # exactly 60 taps
```

`Ctrl+C` to stop.

---

## Controls

While `tap.py` is running in the terminal:

| Key | Action |
|---|---|
| `Space` or `p` | Pause / resume tapping |
| `Ctrl+C` | Stop and exit |

---

## Options

| Flag | Default | Description |
|---|---|---|
| `--x` | 215 | Horizontal coordinate (logical points) |
| `--y` | 466 | Vertical coordinate (logical points) |
| `--interval` | 1.0 | Seconds between taps |
| `--count` | 0 | Taps to perform — 0 = run forever |

---

## Finding coordinates

Phone must be on the right screen and orientation before running.

```bash
python3 src/pick_coords.py    # screenshots phone + opens browser picker
```

Hover over the image to see the ready-to-use `tap.py` command (pixel ÷ scale is automatic).
Click to lock in the coordinate — the command is printed in the terminal.
Press **R** or click **↻ Refresh** to re-take the screenshot without restarting.

To use an existing screenshot instead of capturing a new one:
```bash
python3 src/pick_coords.py --img screenshot.png
```

Known working coordinates:
- Portrait center: `--x 215 --y 466`
- Landscape target (as of Apr 2026): `--x 701 --y 402`

---

## 7-day certificate renewal

Free accounts expire every 7 days. When `scripts/start_wda.py` fails, rebuild WDA:

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
src/tap.py ──HTTP──▶ WDA (port 8100)
                     │
                 iproxy (USB)
                     │
                 WDA runner on iPhone (XCUITest)
```

- `scripts/start_wda.py` — launches `xcodebuild test-without-building` + `iproxy 8100 8100`
- `src/tap.py` — posts W3C pointer actions to WDA HTTP API
- `src/pick_coords.py` — screenshot + browser coordinate picker
- WDA built from: `~/.appium/node_modules/appium-xcuitest-driver/node_modules/appium-webdriveragent/`
