#!/bin/bash
# Start WebDriverAgent on the connected iPhone and forward port 8100.
# Run this once before tap.py. Keep it running in its own terminal.

set -e

# Load device config from .env
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "ERROR: .env not found. Copy .env.example to .env and fill in your values."
    exit 1
fi
set -a; source "$SCRIPT_DIR/.env"; set +a

XCTESTRUN="/tmp/WebDriverAgentRunner.xctestrun"

# Write the xctestrun file
cat > "$XCTESTRUN" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>WebDriverAgentRunner</key>
  <dict>
    <key>IsUITestBundle</key><true/>
    <key>IsXCTRunnerHostedTestBundle</key><true/>
    <key>ProductModuleName</key><string>WebDriverAgentRunner</string>
    <key>SystemAttachmentLifetime</key><string>deleteOnSuccess</string>
    <key>TestBundleDestinationRelativePath</key>
    <string>PlugIns/WebDriverAgentRunner.xctest</string>
    <key>TestHostBundleIdentifier</key>
    <string>com.${TEAM}.WebDriverAgentRunner.xctrunner</string>
    <key>UITargetAppBundleIdentifier</key>
    <string>com.${TEAM}.WebDriverAgentRunner.xctrunner</string>
    <key>UseDestinationArtifacts</key><true/>
    <key>UserAttachmentLifetime</key><string>deleteOnSuccess</string>
  </dict>
  <key>__xctestrun_metadata__</key>
  <dict>
    <key>FormatVersion</key><integer>1</integer>
  </dict>
</dict>
</plist>
EOF

echo "Starting WebDriverAgent on device $UDID ..."
echo "(First run after a 7-day expiry will take ~60s to rebuild)"

# Start iproxy upfront so the tunnel is ready when WDA comes up
echo "Starting iproxy port forward (8100)..."
pkill -f "iproxy 8100" 2>/dev/null || true; sleep 1
iproxy 8100 8100 > /dev/null 2>&1 &
IPROXY_PID=$!
sleep 1

# Start WDA in background
xcodebuild test-without-building \
  -xctestrun "$XCTESTRUN" \
  -destination "id=$UDID" \
  > /tmp/wda.log 2>&1 &
WDA_PID=$!

# Wait for WDA HTTP server
echo -n "Waiting for WDA..."
for i in $(seq 1 40); do
    sleep 2
    if curl -s --max-time 1 "http://127.0.0.1:8100/status" > /dev/null 2>&1; then
        echo " ready!"
        break
    fi
    echo -n "."
done

if curl -s --max-time 3 "http://127.0.0.1:8100/status" > /dev/null 2>&1; then
    echo "WDA is live at http://127.0.0.1:8100"
    echo "Run: python3 tap.py"
else
    echo "ERROR: WDA did not come up. Check /tmp/wda.log"
    kill $WDA_PID 2>/dev/null
    kill $IPROXY_PID 2>/dev/null
    exit 1
fi

# Keep script alive (so WDA stays running)
wait $WDA_PID
