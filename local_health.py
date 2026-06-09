"""
Local health status file — IPC between system_watchdog and reboot watchdog.

system_watchdog writes capture health every quick check (~60s).
camera_reboot_watchdog reads it for local-first hang detection.
"""

import json
import re
import time
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional


def parse_noframes_duration(message: str) -> int:
    """
    Parse NoFrames duration from issue string or log message.

    Handles: 65m, 1h30m, 3h16m, 2d5h15m
    """
    match = re.search(r'NoFrames:(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?', message)
    if not match:
        return 0

    days = int(match.group(1) or 0)
    hours = int(match.group(2) or 0)
    minutes = int(match.group(3) or 0)
    return (days * 24 * 60) + (hours * 60) + minutes


def noframes_minutes_from_issues(issues: List[str]) -> int:
    """Extract NoFrames duration in minutes from issue strings."""
    for issue in issues:
        if 'NoFrames:' in issue:
            return parse_noframes_duration(issue)
    return 0


def parse_noencode_duration(message: str) -> int:
    """Parse NoEncode duration from issue string (same format as NoFrames)."""
    match = re.search(r'NoEncode:(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?', message)
    if not match:
        return 0

    days = int(match.group(1) or 0)
    hours = int(match.group(2) or 0)
    minutes = int(match.group(3) or 0)
    return (days * 24 * 60) + (hours * 60) + minutes


def noencode_minutes_from_issues(issues: List[str]) -> int:
    """Extract NoEncode duration in minutes from issue strings."""
    for issue in issues:
        if 'NoEncode:' in issue:
            return parse_noencode_duration(issue)
    return 0


def hang_minutes_from_health(raw: Dict[str, Any]) -> int:
    """Return hang duration in minutes (NoEncode for soak, NoFrames otherwise)."""
    if raw.get('encode_only_soak'):
        minutes = raw.get('encode_stale_minutes', 0)
        if minutes == 0:
            minutes = noencode_minutes_from_issues(raw.get('issues', []))
        return minutes
    minutes = raw.get('noframes_minutes', 0)
    if minutes == 0:
        minutes = noframes_minutes_from_issues(raw.get('issues', []))
    return minutes


def load_agent_events(path: str) -> List[Dict[str, Any]]:
    """Load hang/recovery event history from JSON file."""
    file_path = Path(path)
    if not file_path.exists():
        return []
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
        events = data.get('events', [])
        return events if isinstance(events, list) else []
    except (json.JSONDecodeError, TypeError, OSError):
        return []


def append_agent_event(
    path: str,
    kind: str,
    detail: str,
    retention_seconds: float = 604800,
    ts: Optional[float] = None,
) -> None:
    """
    Append a hang/recovery event to history (atomic write).

    kind: 'down' (hang started) or 'up' (recovered)
    """
    event_ts = ts if ts is not None else time.time()
    now = time.time()
    events = load_agent_events(path)
    events = [e for e in events if now - float(e.get('ts', 0)) < retention_seconds]
    events.append({
        'ts': event_ts,
        'kind': kind,
        'detail': detail,
    })
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_suffix('.json.tmp')
    with open(tmp_path, 'w') as f:
        json.dump({'events': events}, f, indent=2)
    tmp_path.replace(file_path)


def agent_event_stats(path: str, window_seconds: float = 86400) -> Dict[str, Any]:
    """Summarize hang/recovery events for Prometheus export."""
    now = time.time()
    events = load_agent_events(path)
    downs = [e for e in events if e.get('kind') == 'down']
    ups = [e for e in events if e.get('kind') == 'up']
    downs_24h = [e for e in downs if now - float(e.get('ts', 0)) < window_seconds]
    ups_24h = [e for e in ups if now - float(e.get('ts', 0)) < window_seconds]
    last_down = max((float(e['ts']) for e in downs), default=0)
    last_up = max((float(e['ts']) for e in ups), default=0)
    return {
        'hang_events_total': len(downs),
        'recovery_events_total': len(ups),
        'hangs_last_24h': len(downs_24h),
        'recoveries_last_24h': len(ups_24h),
        'last_hang_timestamp': last_down,
        'last_recovery_timestamp': last_up,
    }


def write_local_health_status(path: str, status: Dict[str, Any]) -> None:
    """Atomically write local health status JSON."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_suffix('.json.tmp')

    with open(tmp_path, 'w') as f:
        json.dump(status, f, indent=2)
    tmp_path.replace(file_path)


def read_local_health_status(path: str, max_age_seconds: float) -> Dict[str, Any]:
    """
    Read local health status file.

    Returns dict with:
        available: file exists and is fresh
        stale: file exists but older than max_age_seconds
        missing: file does not exist
    """
    file_path = Path(path)

    if not file_path.exists():
        return {'available': False, 'missing': True}

    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
    except Exception as e:
        return {'available': False, 'error': str(e)}

    updated_at_unix = data.get('updated_at_unix', 0)
    age_seconds = time.time() - updated_at_unix

    if age_seconds > max_age_seconds:
        return {
            **data,
            'available': False,
            'stale': True,
            'age_seconds': age_seconds,
        }

    return {
        **data,
        'available': True,
        'age_seconds': age_seconds,
    }


def _self_test():
    """Verify NoFrames duration parsing."""
    cases = [
        ("NoFrames:65m", 65),
        ("NoFrames:1h30m", 90),
        ("NoFrames:3h16m", 196),
        ("NoFrames:2d5h15m", 2 * 24 * 60 + 5 * 60 + 15),
        ("Watchdog: ISSUES DETECTED: NoFrames:1h, MotionStuck", 60),
        ("NoFrames:0m", 0),
    ]
    for message, expected in cases:
        result = parse_noframes_duration(message)
        assert result == expected, f"{message!r}: got {result}, expected {expected}"
    print("local_health self-test passed")


if __name__ == '__main__':
    _self_test()
