#!/bin/bash
# Quick Camera System Troubleshooting Guide
# ==========================================

echo "=========================================="
echo "Quick Camera System Check"
echo "=========================================="
echo ""

# 1. Check for running processes
echo "1. Checking for running camera processes..."
PYTHON_PROCS=$(ps aux | grep -E "sec_cam_main|python.*Security-Camera" | grep -v grep)
if [ -n "$PYTHON_PROCS" ]; then
    echo "⚠️  Found running camera processes:"
    echo "$PYTHON_PROCS"
    echo ""
    echo "ACTION: Kill these processes first:"
    echo "  pkill -f sec_cam_main"
    echo ""
else
    echo "✓ No conflicting processes found"
fi

# 2. Check camera hardware
echo ""
echo "2. Testing camera hardware..."
if rpicam-hello --list-cameras 2>&1 | grep -q "Available cameras"; then
    echo "✓ Camera detected"
else
    echo "✗ Camera NOT detected"
    echo ""
    echo "ACTION: Check camera connection:"
    echo "  1. Power off the Pi"
    echo "  2. Reconnect camera ribbon cable"
    echo "  3. Power on and try again"
fi

# 3. Check central server
echo ""
echo "3. Checking central server connectivity..."
CENTRAL_SERVER=$(grep CENTRAL_SERVER_HOST ~/Security-Camera-Agent/config.py | cut -d'"' -f2)
if [ -n "$CENTRAL_SERVER" ]; then
    echo "Central server: $CENTRAL_SERVER"
    if ping -c 1 -W 2 "$CENTRAL_SERVER" >/dev/null 2>&1; then
        echo "✓ Can reach central server"
    else
        echo "✗ Cannot reach central server"
        echo ""
        echo "ACTION: Check network/server:"
        echo "  1. Verify central server is running"
        echo "  2. Check network connection: ping $CENTRAL_SERVER"
        echo "  3. Check firewall settings"
    fi
fi

# 4. Check for libcamera locks
echo ""
echo "4. Checking for stale camera locks..."
LOCKS=$(ls /tmp/*libcamera* 2>/dev/null)
if [ -n "$LOCKS" ]; then
    echo "⚠️  Found stale lock files:"
    echo "$LOCKS"
    echo ""
    echo "ACTION: Remove locks:"
    echo "  sudo rm -f /tmp/*libcamera*"
else
    echo "✓ No stale locks found"
fi

# 5. Check directories
echo ""
echo "5. Checking required directories..."
BASE_DIR="$HOME/Security-Camera-Agent"
if [ -d "$BASE_DIR/tmp" ] && [ -d "$BASE_DIR/tmp/pending" ]; then
    echo "✓ Required directories exist"
else
    echo "⚠️  Missing directories"
    echo ""
    echo "ACTION: Create directories:"
    echo "  mkdir -p $BASE_DIR/tmp/pending"
fi

# 6. Check logs
echo ""
echo "6. Checking recent logs..."
LATEST_LOG=$(ls -t ~/Security-Camera-Agent/logs/runtime_*.log 2>/dev/null | head -1)
if [ -f "$LATEST_LOG" ]; then
    echo "Latest log: $LATEST_LOG"
    echo ""
    echo "Recent errors:"
    tail -20 "$LATEST_LOG" | grep -i error || echo "  No recent errors"
fi

echo ""
echo "=========================================="
echo "Quick Check Complete"
echo "=========================================="
echo ""
echo "Next steps:"
echo "  1. Run: python3 diagnose_camera.py (for detailed check)"
echo "  2. If all checks pass, try starting the system"
echo "  3. Monitor logs: tail -f ~/Security-Camera-Agent/logs/runtime_*.log"
echo ""