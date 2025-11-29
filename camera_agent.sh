#!/bin/bash
#
# camera_agent.sh
# Unified Manager for Camera Agent + Reboot Watchdog Services
#
# This tool provides a consistent command-line interface for managing:
#
#   1. security-camera-agent       (main camera processing service)
#   2. camera-reboot-watchdog      (monitors for camera failures and
#                                  automatically reboots the Pi to
#                                  recover a hung camera or agent)
#
# Both services support:
#   - start / stop / restart
#   - status checks
#   - log viewing and log following
#
# The watchdog service maintains safety limits, prevents reboot loops,
# and contains a persistent history with timestamps and pause states.
#
# NOTE: Watchdog commands require sudo because the service runs as root.
#

# =====================================================================
# CONFIGURATION
# =====================================================================

AGENT_SERVICE="security-camera-agent"
WATCHDOG_SERVICE="camera-reboot-watchdog"
WATCHDOG_HISTORY_FILE="/var/tmp/camera-reboot-history.json"

# =====================================================================
# HELP SCREEN
# =====================================================================

show_help() {
    cat << EOF
Usage: ./camera_agent.sh <command>

Camera Agent Commands:
  agent-start        Start the camera agent service
  agent-stop         Stop the camera agent service
  agent-restart      Restart the camera agent service
  agent-status       Show status of the camera agent
  agent-logs         Show last 1000 log lines
  agent-follow       Follow logs live (Ctrl+C to stop)

Reboot Watchdog Commands (sudo required):
  watchdog-start     Start the reboot watchdog service
  watchdog-stop      Stop the reboot watchdog service
  watchdog-restart   Restart the reboot watchdog service
  watchdog-status    Show watchdog service status
  watchdog-logs      Show last 1000 watchdog log lines
  watchdog-follow    Follow watchdog logs live
  watchdog-history   Show reboot history and pause state

What is the Reboot Watchdog?
----------------------------
The reboot watchdog continuously monitors camera health by analyzing
log output from the camera agents. When it detects a prolonged failure
(e.g., 'NoFrames' for too long), it automatically attempts to recover
the system by rebooting the Raspberry Pi.

Safety features include:
  - Cooldown between reboots
  - Max reboots per hour limit
  - Auto-pause when limits exceeded
  - Persistent history across reboots
  - Ability to be manually enabled/disabled

Examples:
  ./camera_agent.sh agent-status
  ./camera_agent.sh agent-logs
  sudo ./camera_agent.sh watchdog-status
  sudo ./camera_agent.sh watchdog-history
EOF
}

# =====================================================================
# CAMERA AGENT FUNCTIONS
# =====================================================================

agent_start() {
    echo "▶️  Starting $AGENT_SERVICE..."
    sudo systemctl start "$AGENT_SERVICE"
    systemctl status "$AGENT_SERVICE"
}

agent_stop() {
    echo "⏹️  Stopping $AGENT_SERVICE..."
    sudo systemctl stop "$AGENT_SERVICE"
    systemctl status "$AGENT_SERVICE"
}

agent_restart() {
    echo "🔄 Restarting $AGENT_SERVICE..."
    sudo systemctl restart "$AGENT_SERVICE"
    systemctl status "$AGENT_SERVICE"
}

agent_status() {
    echo "📌 Status for $AGENT_SERVICE:"
    systemctl status "$AGENT_SERVICE"
}

agent_logs() {
    echo "📜 Showing last 1000 log lines for $AGENT_SERVICE..."
    sudo journalctl -u "$AGENT_SERVICE" -n 1000 --no-pager
}

agent_follow() {
    echo "📡 Following logs for $AGENT_SERVICE..."
    sudo journalctl -u "$AGENT_SERVICE" -f
}

# =====================================================================
# WATCHDOG FUNCTIONS (require sudo)
# =====================================================================

require_sudo() {
    if [[ $EUID -ne 0 ]]; then
        echo "⚠️  This command requires sudo."
        echo "Usage: sudo ./camera_agent.sh $1"
        exit 1
    fi
}

watchdog_start() {
    require_sudo "watchdog-start"
    echo "▶️  Starting $WATCHDOG_SERVICE..."
    systemctl start "$WATCHDOG_SERVICE"
    systemctl status "$WATCHDOG_SERVICE"
}

watchdog_stop() {
    require_sudo "watchdog-stop"
    echo "⏹️  Stopping $WATCHDOG_SERVICE..."
    systemctl stop "$WATCHDOG_SERVICE"
    systemctl status "$WATCHDOG_SERVICE"
}

watchdog_restart() {
    require_sudo "watchdog-restart"
    echo "🔄 Restarting $WATCHDOG_SERVICE..."
    systemctl restart "$WATCHDOG_SERVICE"
    systemctl status "$WATCHDOG_SERVICE"
}

watchdog_status() {
    require_sudo "watchdog-status"
    echo "📌 Status for $WATCHDOG_SERVICE:"
    systemctl status "$WATCHDOG_SERVICE"
}

watchdog_logs() {
    require_sudo "watchdog-logs"
    echo "📜 Showing last 1000 watchdog log lines..."
    journalctl -u "$WATCHDOG_SERVICE" -n 1000 --no-pager
}

watchdog_follow() {
    require_sudo "watchdog-follow"
    echo "📡 Following watchdog logs..."
    journalctl -u "$WATCHDOG_SERVICE" -f
}

watchdog_history() {
    echo "📊 Reboot Watchdog History"
    echo "=========================="
    echo ""

    if [[ ! -f "$WATCHDOG_HISTORY_FILE" ]]; then
        echo "No reboot history found."
        return
    fi

    cat "$WATCHDOG_HISTORY_FILE"
    echo ""

    # Pretty formatting through Python
    python3 << 'EOF'
import json
from datetime import datetime

try:
    with open("/var/tmp/camera-reboot-history.json") as f:
        data = json.load(f)

    print("Formatted History:")
    print("------------------")

    reboots = data.get("reboots", [])
    pause_until = data.get("pause_until")

    if reboots:
        print("\nRecent Reboots:")
        for t in reboots:
            print(f" • {datetime.fromtimestamp(t).strftime('%Y-%m-%d %H:%M:%S')}")
    else:
        print("No reboots recorded.")

    print("\nPause State:")
    if pause_until:
        ts = datetime.fromtimestamp(pause_until)
        print(f"Paused until: {ts}")
    else:
        print("Not paused.")

except Exception as e:
    print(f"Error reading history: {e}")
EOF
}

# =====================================================================
# COMMAND HANDLER
# =====================================================================

case "$1" in
    agent-start)     agent_start ;;
    agent-stop)      agent_stop ;;
    agent-restart)   agent_restart ;;
    agent-status)    agent_status ;;
    agent-logs)      agent_logs ;;
    agent-follow)    agent_follow ;;

    watchdog-start)   watchdog_start ;;
    watchdog-stop)    watchdog_stop ;;
    watchdog-restart) watchdog_restart ;;
    watchdog-status)  watchdog_status ;;
    watchdog-logs)    watchdog_logs ;;
    watchdog-follow)  watchdog_follow ;;
    watchdog-history) watchdog_history ;;

    help|-h|--help|"") show_help ;;

    *)
        echo "❌ Unknown command: $1"
        show_help
        exit 1
        ;;
esac
