#!/bin/bash
# ============================================================================
# run.sh — Launch Security Camera with immediate output and graceful Ctrl+C
# ============================================================================

APP_DIR="/home/pi/Security-Camera-Agent"
LOG_DIR="$APP_DIR/logs"
LOG_FILE="$LOG_DIR/runtime_$(date +%Y%m%d).log"
PYTHON_BIN="/usr/bin/python3"

mkdir -p "$LOG_DIR"

echo "===================================================" | tee -a "$LOG_FILE"
echo "Starting Security Camera: $(date)" | tee -a "$LOG_FILE"
echo "Logging to: $LOG_FILE" | tee -a "$LOG_FILE"
echo "===================================================" | tee -a "$LOG_FILE"

cd "$APP_DIR"

# --- Trap Ctrl+C and forward it to python ---
trap 'echo "🔴 Caught Ctrl+C — stopping camera..."; kill $PY_PID 2>/dev/null; wait $PY_PID; exit 0' SIGINT SIGTERM

# --- Run Python with unbuffered output ---
#  -u : forces Python stdout/stderr to be unbuffered
# stdbuf -oL ensures line-buffering for all piped output
stdbuf -oL -eL $PYTHON_BIN -u sec_cam_main.py 2>&1 | tee -a "$LOG_FILE" &
PY_PID=$!

# --- Wait for python process to exit ---
wait $PY_PID
EXIT_CODE=$?

echo "===================================================" | tee -a "$LOG_FILE"
echo "Camera stopped at: $(date) with exit code $EXIT_CODE" | tee -a "$LOG_FILE"
echo "===================================================" | tee -a "$LOG_FILE"

