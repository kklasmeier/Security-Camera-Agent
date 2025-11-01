#!/bin/bash
# ============================================================================
# killpython.sh — Smart Python process killer (handles zombies)
# ============================================================================
# This script terminates all active Python processes (python + python3)
# and reaps any zombie (<defunct>) processes by killing their parent.
#
# Author: Kevin's AI Assistant
# ============================================================================

echo "🔍 Searching for Python processes..."
ps -eo pid,ppid,stat,cmd | grep '[p]ython' || echo "No Python processes found."

echo ""
echo "🧹 Killing active Python processes..."
# Kill normal python processes first
pkill -9 -f "python.*Pixil\.py"
pkill -9 -f "sudo.*python"
pkill -9 -f "python[0-9]*"

sleep 1

echo ""
echo "🔍 Checking for zombie (defunct) Python processes..."
ZOMBIES=$(ps -eo pid,ppid,stat,cmd | awk '/[pP]ython/ && $3 ~ /Z/ {print $1":"$2}')

if [ -n "$ZOMBIES" ]; then
    echo "Found zombie Python processes:"
    echo "$ZOMBIES" | while IFS=: read -r ZPID PARENT; do
        echo "⚰️  Zombie PID=$ZPID (parent PID=$PARENT)"
        PNAME=$(ps -p "$PARENT" -o comm=)
        echo "   → Parent process: $PNAME ($PARENT)"
        echo "   → Attempting to kill parent..."
        sudo kill -9 "$PARENT" 2>/dev/null && echo "   ✅ Parent killed." || echo "   ⚠️ Failed to kill parent."
    done
else
    echo "No zombie Python processes found."
fi

sleep 1

echo ""
echo "🔁 Final verification..."
REMAINING=$(ps aux | grep '[p]ython')

if [ -n "$REMAINING" ]; then
    echo "⚠️  Some Python processes still remain:"
    echo "$REMAINING"
else
    echo "✅ All Python processes have been terminated."
fi

echo ""
echo "🧽 Cleanup complete."

