#!/usr/bin/env bash
set -eo pipefail

TARGET_URL="${1:-http://localhost:5000/}"
CHROME_PATH="C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
POWERSHELL_BIN="/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"

echo "========================================================="
echo " 🌐 Setting up Chrome Browser Bridge for UI Validation"
echo " Target URL: ${TARGET_URL}"
echo "========================================================="

# 1. Verify PowerShell binary availability in WSL
if [ ! -f "$POWERSHELL_BIN" ]; then
    echo "❌ PowerShell executable not found at $POWERSHELL_BIN"
    exit 1
fi

# 2. Verify socat is installed in WSL
if ! command -v socat &>/dev/null; then
    echo "⚠️ socat is not installed in WSL. Installing socat..."
    sudo apt-get update -y && sudo apt-get install -y socat
fi

# 3. Kill lingering Chrome instances and launch fresh debug instance on Windows
echo "🚀 Terminating old Chrome processes on Windows and launching fresh debug instance..."
"$POWERSHELL_BIN" -Command "
    Stop-Process -Name chrome -Force -ErrorAction SilentlyContinue;
    Start-Sleep -Milliseconds 800;
    Start-Process '$CHROME_PATH' -ArgumentList '--remote-debugging-port=9222','--remote-debugging-address=0.0.0.0','--user-data-dir=C:\tmp\chrome-debug','$TARGET_URL'
"

# 4. Resolve Windows Host IP from WSL2
WIN_IP=$(ip route show | grep -i default | awk '{ print $3 }')
if [ -z "$WIN_IP" ]; then
    echo "❌ Could not determine Windows host IP from default route"
    exit 1
fi
echo "📍 Windows Host IP detected: $WIN_IP"

# 5. Refresh WSL2-to-Windows socat tunnel on port 9222
echo "🔌 Starting socat bridge (WSL 127.0.0.1:9222 -> Windows ${WIN_IP}:9222)..."
killall socat 2>/dev/null || true
socat TCP-LISTEN:9222,fork,reuseaddr TCP:"$WIN_IP":9222 &
SOCAT_PID=$!
sleep 1.5

# 6. Verify CDP connection with retry loop
echo "🔍 Verifying CDP connection at http://127.0.0.1:9222/json/version..."
SUCCESS=0
for i in {1..10}; do
    if RESPONSE=$(curl -s --connect-timeout 2 http://127.0.0.1:9222/json/version 2>/dev/null); then
        if echo "$RESPONSE" | grep -q "webSocketDebuggerUrl"; then
            echo "✅ Chrome CDP connection successfully established!"
            echo "---------------------------------------------------------"
            echo "$RESPONSE" | grep -E '"(Browser|webSocketDebuggerUrl)"'
            echo "---------------------------------------------------------"
            SUCCESS=1
            break
        fi
    fi
    echo "⏳ Waiting for Chrome CDP to respond (attempt $i/10)..."
    sleep 1
done

if [ $SUCCESS -eq 0 ]; then
    echo "❌ Failed to connect to Chrome CDP on port 9222 after 10 attempts."
    exit 1
fi

echo "🎉 Chrome is ready for UI visual automation and subagent validation!"
exit 0
