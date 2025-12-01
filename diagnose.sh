#!/bin/bash
echo "=== Camera Identity ==="
grep CAMERA_ID ~/Security-Camera-Agent/config_local.py
echo ""

echo "=== Recent Watchdog Checks ==="
sudo journalctl -u camera-reboot-watchdog -n 20 --no-pager | grep "WATCHDOG REBOOT"
echo ""

echo "=== Test API Query ==="
CAMERA_ID=$(grep "CAMERA_ID =" ~/Security-Camera-Agent/config_local.py | cut -d'"' -f2)
echo "Camera ID: $CAMERA_ID"

ONE_HOUR_AGO=$(date -u -d '1 hour ago' '+%Y-%m-%dT%H:%M:%S')
echo "Querying from: $ONE_HOUR_AGO"

curl -s "http://192.168.1.26:8000/api/v1/logs?source=$CAMERA_ID&level=ERROR&after=$ONE_HOUR_AGO&limit=10" | python3 -m json.tool

