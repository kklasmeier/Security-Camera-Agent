#!/bin/bash
# Test Security Camera System
# ============================
# Runs the camera system and monitors for proper operation

echo "=========================================="
echo "Security Camera System Test"
echo "=========================================="
echo ""

# Check if already running
if pgrep -f "sec_cam_main" > /dev/null; then
    echo "  Camera system already running!"
    echo "Stop it first with: pkill -f sec_cam_main"
    exit 1
fi

# Check camera
echo "1. Testing camera hardware..."
if rpicam-jpeg -o /tmp/test.jpg --timeout 1 --width 640 --height 480 2>/dev/null; then
    echo " Camera is working"
    rm -f /tmp/test.jpg
else
    echo " Camera test failed"
    exit 1
fi

# Check central server
echo ""
echo "2. Testing central server..."
CENTRAL_SERVER=$(grep CENTRAL_SERVER_HOST ~/Security-Camera-Agent/config.py | head -1 | cut -d'"' -f2 | grep -v '{' || echo "192.168.1.26")
echo "Central server: $CENTRAL_SERVER"

if ping -c 1 -W 2 "$CENTRAL_SERVER" >/dev/null 2>&1; then
    echo " Can reach central server"
else
    echo " Cannot reach central server at $CENTRAL_SERVER"
    echo ""
    echo "Is the central server running? Check with:"
    echo "  ssh user@$CENTRAL_SERVER"
    echo "  systemctl status security-camera-central  # or similar"
    exit 1
fi

echo ""
echo "3. Starting camera system..."
echo "   (Will run for 2 minutes, then auto-stop)"
echo "   Watch for motion detection logs"
echo "   Try triggering motion by walking in front of camera!"
echo ""
echo "=========================================="

cd ~/Security-Camera-Agent

# Start system in background
./run.sh &
CAMERA_PID=$!

echo "Camera PID: $CAMERA_PID"
echo ""

# Function to monitor logs
monitor_logs() {
    local duration="$1"
    local log_file="logs/runtime_$(date +%Y%m%d).log"
    
    echo "Monitoring for $duration seconds..."
    echo ""
    
    local start_time
    start_time=$(date +%s)
    local last_motion_check=0
    local motion_events=0

    # sanitize duration just in case
    duration=$(echo "$duration" | tr -dc '0-9')

    while true; do
        local current_time
        current_time=$(date +%s)
        local elapsed=$((current_time - start_time))

        # --- Safety: make sure elapsed and last_motion_check are numeric ---
        elapsed=$(echo "$elapsed" | tr -dc '0-9')
        last_motion_check=$(echo "$last_motion_check" | tr -dc '0-9')

        printf "DEBUG: elapsed=[%s] last_motion_check=[%s]\n" "$elapsed" "$last_motion_check"

        if (( elapsed >= duration )); then
            break
        fi
        
        # Check for motion detection
        if [ -f "$log_file" ]; then
            local motion_count
            motion_count=$(grep -c "MOTION DETECTED" "$log_file" 2>/dev/null || echo 0)
            printf "DEBUG: motion_count=[%s] motion_events=[%s]\n" "$motion_count" "$motion_events"
            if (( motion_count > motion_events )); then
                echo "🎯 MOTION EVENT DETECTED! (Total: $motion_count)"
                motion_events=$motion_count
            fi
            
            # Safe arithmetic evaluation
            if (( elapsed > 0 )); then
                if (( elapsed % 30 == 0 )); then
                    if (( elapsed != last_motion_check )); then
                        checks=$(grep -c "Motion check" "$log_file" 2>/dev/null || echo 0)
                        echo "Status: ${elapsed}/${duration}s - Motion checks: ${checks}, Events: ${motion_events}"
                        last_motion_check=$elapsed
                    fi
                fi
            fi
        fi
        
        sleep 1
    done
    
    echo ""
    echo "=========================================="
    echo "Test Complete!"
    echo "=========================================="
    echo ""
    echo "Summary:"
    echo "  Runtime: ${duration} seconds"
    echo "  Motion events detected: ${motion_events}"
    
    if (( motion_events > 0 )); then
        echo ""
        echo "✓ System is WORKING!"
        echo ""
        echo "Motion events were captured. Check:"
        echo "  - Pending files: ls -lh ~/Security-Camera-Agent/tmp/pending/"
        echo "  - Transferred files: ls -lh ~/Security-Camera-Agent/security_footage/pictures/"
    else
        echo ""
        echo "⚠️  No motion detected during test"
        echo ""
        echo "This could mean:"
        echo "  1. System is in cooldown (65 second cooldown after last event)"
        echo "  2. No motion occurred in camera view"
        echo "  3. Motion sensitivity is too low"
        echo ""
        echo "Check the logs:"
        echo "  tail -50 ~/Security-Camera-Agent/logs/runtime_*.log"
    fi
    
    echo ""
    echo "Last 10 log entries:"
    echo "----------------------------------------"
    tail -10 "$log_file" 2>/dev/null || echo "No log file found"
}



# Monitor for 2 minutes
monitor_logs 120

# Stop the camera
echo ""
echo "Stopping camera system..."
kill $CAMERA_PID 2>/dev/null
sleep 2

# Force kill if still running
if ps -p $CAMERA_PID > /dev/null 2>&1; then
    kill -9 $CAMERA_PID 2>/dev/null
fi

echo " Camera stopped"
echo ""
echo "To run the system normally:"
echo "  cd ~/Security-Camera-Agent"
echo "  ./run.sh"
echo ""
