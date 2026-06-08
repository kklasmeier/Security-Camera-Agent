#!/usr/bin/env bash
# Writes Raspberry Pi health metrics for Prometheus textfile collector.

OUT="/var/lib/node_exporter/textfile_collector/pi_health.prom"
TMP="$(mktemp)"

emit() {
  # $1 metric, $2 value, $3 optional labels
  if [ -n "$3" ]; then
    echo "$1{$3} $2" >> "$TMP"
  else
    echo "$1 $2" >> "$TMP"
  fi
}

# Throttle/undervoltage flags
if command -v vcgencmd >/dev/null 2>&1; then
  THROTTLED_HEX=$(vcgencmd get_throttled 2>/dev/null | awk -F= '{print $2}')
  [ -z "$THROTTLED_HEX" ] && THROTTLED_HEX=0x0
  DEC=$((THROTTLED_HEX))
  emit raspberrypi_throttled_raw "$DEC"
  emit raspberrypi_throttled_is_undervolting_now "$(( (DEC & 0x1) > 0 ? 1 : 0 ))"
  emit raspberrypi_throttled_has_undervolted_since_boot "$(( (DEC & 0x10000) > 0 ? 1 : 0 ))"
  emit raspberrypi_throttled_is_thermally_throttled_now "$(( (DEC & 0x4) > 0 ? 1 : 0 ))"
fi

# Temperature (C)
if command -v vcgencmd >/dev/null 2>&1; then
  TEMP=$(vcgencmd measure_temp 2>/dev/null | tr -dc '0-9.\n')
  [ -n "$TEMP" ] && emit raspberrypi_cpu_temp_c "$TEMP"
fi

# Core voltage
if command -v vcgencmd >/dev/null 2>&1; then
  VCORE=$(vcgencmd measure_volts core 2>/dev/null | tr -dc '0-9.\n')
  [ -n "$VCORE" ] && emit raspberrypi_core_volts "$VCORE"
fi

# Root FS usage (percent)
USED=$(df -P / | awk 'NR==2{print $5}' | tr -d '%')
[ -n "$USED" ] && emit raspberrypi_rootfs_used_percent "$USED"

# WireGuard handshake age (optional)
if command -v wg >/dev/null 2>&1; then
  while read -r NAME; do
    AGE=$(wg show "$NAME" latest-handshakes 2>/dev/null | awk '{print $2}')
    NOW=$(date +%s)
    if [ -n "$AGE" ] && [ "$AGE" -gt 0 ]; then
      emit raspberrypi_wg_last_handshake_age_seconds "$((NOW-AGE))" "interface=\"$NAME\""
    fi
  done < <(wg show interfaces 2>/dev/null | tr ' ' '\n')
fi

# WiFi link quality (use full path — cron PATH omits /sbin)
IW=""
for _iw in /usr/sbin/iw /sbin/iw; do
  [ -x "$_iw" ] && IW="$_iw" && break
done
if [ -n "$IW" ]; then
  LINK=$("$IW" dev wlan0 link 2>/dev/null || true)
  if echo "$LINK" | grep -q "Connected to"; then
    emit camera_wifi_connected 1
    RSSI=$(echo "$LINK" | awk '/signal/ {print $2}')
    [ -n "$RSSI" ] && emit camera_wifi_signal_dbm "$RSSI"
  else
    emit camera_wifi_connected 0
  fi
fi

# Security camera agent health (camera nodes only)
CAMERA_AGENT="/home/pi/Security-Camera-Agent"
if [ -d "$CAMERA_AGENT" ]; then
  LH="$CAMERA_AGENT/var/local_health.json"
  if [ -f "$LH" ]; then
    read -r HEALTH NOFRAMES NOENCODE THREADS ENCODE_SOAK ENCODE_CHUNKS ENCODE_STALE ENCODE_HEALTH <<< "$(python3 - "$LH" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
encode_only = int(bool(d.get("encode_only_soak", False)))
healthy = int(bool(d.get("healthy", False)))
noframes = int(d.get("noframes_minutes") or 0)
noencode = int(d.get("encode_stale_minutes") or 0)
threads = int(d.get("threads_alive") or 0)
chunks = int(d.get("encode_chunks") or 0)
stale = int(d.get("encode_stale_seconds") or 0)
encode_healthy = int(bool(d.get("encode_healthy", False)))
print(healthy, noframes, noencode, threads, encode_only, chunks, stale, encode_healthy)
PY
)"
    emit camera_agent_healthy "$HEALTH"
    emit camera_agent_noframes_minutes "$NOFRAMES"
    emit camera_agent_noencode_minutes "$NOENCODE"
    emit camera_agent_threads_alive "$THREADS"
    emit camera_agent_encode_only_soak "$ENCODE_SOAK"
    emit camera_agent_encode_chunks "$ENCODE_CHUNKS"
    emit camera_agent_encode_stale_seconds "$ENCODE_STALE"
    emit camera_agent_encode_healthy "$ENCODE_HEALTH"
  fi

  if curl -m 2 -sf http://192.168.1.26:8000/ >/dev/null 2>&1; then
    emit camera_central_api_reachable 1
  else
    emit camera_central_api_reachable 0
  fi

  if mountpoint -q "$CAMERA_AGENT/security_footage" 2>/dev/null; then
    emit camera_nfs_mounted 1
  else
    emit camera_nfs_mounted 0
  fi
fi

sudo mv "$TMP" "$OUT"
sudo chown nodeexp:nodeexp "$OUT"
sudo chmod 644 "$OUT"
