#!/bin/bash
#
# camera_agent.sh
# Helper tool for managing the Security Camera Agent service
#

SERVICE="security-camera-agent"
LOG_FILE="/var/log/syslog"   # journalctl will still be used for accuracy

show_help() {
    cat << EOF
Usage: $0 <command>

Commands:
  restart        Restart the security camera agent
  status         Show the status of the agent service
  logs           Show the last 1000 log lines for the agent
  follow         Continuously follow new log entries (Ctrl+C to stop)
  help           Show this help message

Examples:
  $0 restart
  $0 status
  $0 logs
  $0 follow

EOF
}

restart_service() {
    echo "🔄 Restarting $SERVICE..."
    sudo systemctl restart "$SERVICE"
    if [[ $? -eq 0 ]]; then
        echo "✅ Restarted successfully."
    else
        echo "❌ Failed to restart."
    fi
}

show_status() {
    echo "📌 Status for $SERVICE:"
    systemctl status "$SERVICE"
}

show_logs() {
    echo "📜 Showing last 1000 log lines for $SERVICE..."
    sudo journalctl -u "$SERVICE" -n 1000 --no-pager
}

follow_logs() {
    echo "📡 Following logs for $SERVICE..."
    echo "Press Ctrl+C to stop."
    sudo journalctl -u "$SERVICE" -f
}

# No args? Show help.
if [[ $# -eq 0 ]]; then
    show_help
    exit 0
fi

# Handle commands
case "$1" in
    restart)
        restart_service
        ;;
    status)
        show_status
        ;;
    logs)
        show_logs
        ;;
    follow)
        follow_logs
        ;;
    help|-h|--help)
        show_help
        ;;
    *)
        echo "❌ Unknown command: $1"
        show_help
        exit 1
        ;;
esac
