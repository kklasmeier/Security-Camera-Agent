#!/bin/bash
# find_empty_logs.sh - Find sources of empty log messages

echo "=================================================="
echo "Searching for Empty Log Calls"
echo "=================================================="
echo ""

cd ~/Security-Camera-Agent

echo "1. Direct empty log calls:"
echo "   log('') or log(\"\")"
echo "--------------------------------------------------"
grep -rn "log('')" --include="*.py" . 2>/dev/null || echo "   None found"
grep -rn 'log("")' --include="*.py" . 2>/dev/null || echo "   None found"
echo ""

echo "2. Empty f-string logs:"
echo "   log(f'') or log(f\"\")"
echo "--------------------------------------------------"
grep -rn "log(f'')" --include="*.py" . 2>/dev/null || echo "   None found"
grep -rn 'log(f"")' --include="*.py" . 2>/dev/null || echo "   None found"
echo ""

echo "3. Suspicious patterns (might create empty logs):"
echo "   Variables that could be empty"
echo "--------------------------------------------------"
grep -rn 'log(.*msg.*)' --include="*.py" . | grep -v "message" | head -10 || echo "   None found"
echo ""

echo "4. Multi-line log structures (separators):"
echo "   Looking for separator patterns"
echo "--------------------------------------------------"
grep -rn 'log.*"="' --include="*.py" . | head -5 || echo "   None found"
grep -rn "log.*'='" --include="*.py" . | head -5 || echo "   None found"
echo ""

echo "5. Check for conditional logs that might be empty:"
echo "   if/else patterns"
echo "--------------------------------------------------"
grep -B2 -A2 "log(.*if.*else" --include="*.py" . | head -20 || echo "   None found"
echo ""

echo "=================================================="
echo "Recommendations:"
echo "=================================================="
echo "1. Replace log('') with log('---') for separators"
echo "2. Add guards: if msg: log(msg)"
echo "3. Use log('Status: OK') instead of log(status) if status might be empty"
echo ""
