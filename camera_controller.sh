#!/bin/bash
# =============================================================================
# camera_controller.sh — Unified Control Script for Security Camera Agent
# =============================================================================
# Usage:
#   ./camera_controller.sh start     → Start the Security Camera Agent service
#   ./camera_controller.sh stop      → Stop the Security Camera Agent service
#   ./camera_controller.sh restart   → Restart the Security Camera Agent service
#   ./camera_controller.sh status    → Show current service status
#   ./camera_controller.sh log       → Follow service logs live
#
# Description:
#   Simplifies management of the "security-camera-agent.service" systemd unit.
#   Provides clear feedback messages and human-friendly icons.
#
# Behavior:
#   • If run with no arguments, shows detailed help and usage information.
#   • Uses 'sudo' where needed for systemctl and journalctl commands.
#   • Intended for Raspberry Pi nodes running the Security-Camera-Agent.
#
# Example:
#   ./camera_controller.sh restart
#   ./camera_controller.sh log
#
# Author: Kevin’s Security Camera System
# =============================================================================

SERVICE="security-camera-agent.service"

show_help() {
    cat <<'EOF'
===============================================================================
📸 Security Camera Agent — Control Utility
===============================================================================
Usage:
  ./camera_controller.sh start     → Start the Security Camera Agent service
  ./camera_controller.sh stop      → Stop the Security Camera Agent service
  ./camera_controller.sh restart   → Restart the Security Camera Agent service
  ./camera_controller.sh status    → Display current status
  ./camera_controller.sh log       → Follow logs live (Ctrl+C to exit)
  ./camera_controller.sh help      → Show this help message

Description:
  This tool manages the systemd service:
      security-camera-agent.service

  It eliminates the need to remember long 'systemctl' commands.
  Use it to quickly control, restart, or debug your camera service.

Examples:
  ./camera_controller.sh restart
  ./camera_controller.sh status
  ./camera_controller.sh log
===============================================================================
EOF
}

# If no arguments, display help
if [ -z "$1" ]; then
    show_help
    exit 0
fi

case "$1" in
    start)
        echo "🚀 Starting Security Camera Agent..."
        sudo systemctl start "$SERVICE"
        ;;
    stop)
        echo "🛑 Stopping Security Camera Agent..."
        sudo systemctl stop "$SERVICE"
        ;;
    restart)
        echo "🔄 Restarting Security Camera Agent..."
        sudo systemctl restart "$SERVICE"
        ;;
    status)
        echo "📋 Checking service status..."
        sudo systemctl status "$SERVICE" --no-pager
        ;;
    log)
        echo "📜 Following logs (Ctrl+C to exit)..."
        sudo journalctl -u "$SERVICE" -f
        ;;
    help|-h|--help)
        show_help
        ;;
    *)
        echo "❌ Unknown option: $1"
        show_help
        exit 1
        ;;
esac
