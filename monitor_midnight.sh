#!/bin/bash
# Monitor camera agent through midnight to verify fix
# Run this before midnight to monitor the rotation

CAMERA_DIR="/home/pi/Security-Camera-Agent"
LOG_DIR="$CAMERA_DIR/logs"
MONITOR_LOG="/tmp/midnight_monitor.log"

echo "=================================================="  | tee -a "$MONITOR_LOG"
echo "Midnight Log Rotation Monitor"                      | tee -a "$MONITOR_LOG"
echo "Started: $(date)"                                   | tee -a "$MONITOR_LOG"
echo "=================================================="  | tee -a "$MONITOR_LOG"
echo ""                                                    | tee -a "$MONITOR_LOG"

# Function to check if process is healthy
check_health() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    
    # Check if service is running
    if ! systemctl is-active --quiet security-camera-agent.service; then
        echo "[$timestamp] ✗ SERVICE DOWN!" | tee -a "$MONITOR_LOG"
        return 1
    fi
    
    # Check if process exists
    if ! pgrep -f "sec_cam_main.py" > /dev/null; then
        echo "[$timestamp] ✗ PROCESS NOT FOUND!" | tee -a "$MONITOR_LOG"
        return 1
    fi
    
    # Get process info
    local pid=$(pgrep -f "sec_cam_main.py")
    local cpu=$(ps -p $pid -o %cpu= 2>/dev/null || echo "0")
    local mem=$(ps -p $pid -o %mem= 2>/dev/null || echo "0")
    
    # Check latest log file
    local today_log="$LOG_DIR/runtime_$(date +%Y%m%d).log"
    local log_size=$(stat -c%s "$today_log" 2>/dev/null || echo "0")
    local log_mtime=$(stat -c%Y "$today_log" 2>/dev/null || echo "0")
    local log_age=$(( $(date +%s) - $log_mtime ))
    
    echo "[$timestamp] ✓ Healthy - PID:$pid CPU:${cpu}% MEM:${mem}% LogSize:${log_size}B LogAge:${log_age}s" | tee -a "$MONITOR_LOG"
    
    # Warning if log hasn't been updated in 60 seconds
    if [ $log_age -gt 60 ]; then
        echo "[$timestamp] ⚠ WARNING: Log file not updated in ${log_age} seconds!" | tee -a "$MONITOR_LOG"
    fi
    
    return 0
}

# Monitor loop
echo "Monitoring every 30 seconds..."
echo "Press Ctrl+C to stop"
echo ""

while true; do
    current_hour=$(date +%H)
    current_minute=$(date +%M)
    
    # Increase monitoring frequency around midnight (23:55 to 00:05)
    if [ "$current_hour" = "23" ] && [ "$current_minute" -ge "55" ]; then
        interval=5  # Check every 5 seconds before midnight
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ENTERING HIGH-FREQUENCY MODE (5s intervals)" | tee -a "$MONITOR_LOG"
    elif [ "$current_hour" = "00" ] && [ "$current_minute" -le "05" ]; then
        interval=5  # Check every 5 seconds after midnight
    else
        interval=30  # Normal monitoring
    fi
    
    # Check health
    if ! check_health; then
        echo ""
        echo "=================================================="  | tee -a "$MONITOR_LOG"
        echo "FAILURE DETECTED!"                                  | tee -a "$MONITOR_LOG"
        echo "Time: $(date)"                                      | tee -a "$MONITOR_LOG"
        echo "=================================================="  | tee -a "$MONITOR_LOG"
        echo ""
        echo "Collecting diagnostics..."
        
        # Collect diagnostics
        echo "--- Service Status ---" >> "$MONITOR_LOG"
        sudo systemctl status security-camera-agent.service --no-pager >> "$MONITOR_LOG" 2>&1
        
        echo "--- Recent Journald ---" >> "$MONITOR_LOG"
        sudo journalctl -u security-camera-agent.service -n 50 --no-pager >> "$MONITOR_LOG" 2>&1
        
        echo "--- Process List ---" >> "$MONITOR_LOG"
        ps aux | grep -E "(sec_cam|python)" >> "$MONITOR_LOG" 2>&1
        
        echo ""
        echo "Diagnostics saved to: $MONITOR_LOG"
        echo ""
        
        # Exit monitoring loop
        break
    fi
    
    # Special logging at midnight
    if [ "$current_hour" = "00" ] && [ "$current_minute" = "00" ]; then
        echo ""
        echo "=================================================="  | tee -a "$MONITOR_LOG"
        echo "MIDNIGHT ROTATION DETECTED"                         | tee -a "$MONITOR_LOG"
        echo "Time: $(date)"                                      | tee -a "$MONITOR_LOG"
        echo "=================================================="  | tee -a "$MONITOR_LOG"
        
        # Check both today and yesterday log files
        yesterday=$(date -d "yesterday" +%Y%m%d 2>/dev/null || date -v-1d +%Y%m%d 2>/dev/null)
        today=$(date +%Y%m%d)
        
        echo "Yesterday's log: $(ls -lh "$LOG_DIR/runtime_${yesterday}.log" 2>/dev/null || echo "NOT FOUND")" | tee -a "$MONITOR_LOG"
        echo "Today's log: $(ls -lh "$LOG_DIR/runtime_${today}.log" 2>/dev/null || echo "NOT FOUND")" | tee -a "$MONITOR_LOG"
        
        # Continue monitoring for 5 more minutes after midnight
        echo "Continuing high-frequency monitoring for 5 minutes..." | tee -a "$MONITOR_LOG"
        echo ""
    fi
    
    sleep $interval
done

echo ""
echo "=================================================="  | tee -a "$MONITOR_LOG"
echo "Monitoring ended: $(date)"                          | tee -a "$MONITOR_LOG"
echo "Full log saved to: $MONITOR_LOG"                    | tee -a "$MONITOR_LOG"
echo "=================================================="  | tee -a "$MONITOR_LOG"