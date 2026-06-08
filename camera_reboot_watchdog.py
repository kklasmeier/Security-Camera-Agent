#!/usr/bin/env python3
"""
Security Camera System - Reboot Watchdog Service
=================================================
Monitors camera health and automatically reboots on hang detection.

This service runs as pi with CAP_SYS_BOOT (separate from camera agent) and:
- Reads local health status from system_watchdog (primary)
- Optionally queries central server for fleet visibility (not required for reboot)
- Detects NoFrames errors lasting > 60 minutes
- Checks safety limits before rebooting
- Implements rate limiting and pause mechanism

Design:
- Runs independently from camera agent (separate systemd service)
- Uses shared camera agent modules (logger.py, api_client.py, config.py)
- Executes reboot via /sbin/reboot (pi user with CAP_SYS_BOOT)
- Monitors single camera (one instance per camera Pi)

Safety Mechanisms:
- 5-minute cooldown between reboots
- 5 reboots/hour limit triggers 24-hour pause
- Skips reboot if camera is streaming
- Skips reboot if camera agent is not running (deploy/maintenance)
- Manual disable via flag file
- Comprehensive logging of all decisions
"""

import sys
import time
import json
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List

# Add camera agent directory to Python path
CAMERA_AGENT_DIR = Path("/home/pi/Security-Camera-Agent")
sys.path.insert(0, str(CAMERA_AGENT_DIR))

# Import from camera agent modules (shared code)
from logger import EnhancedLogger, log
from api_client import APIClient
from config import config
from local_health import read_local_health_status, parse_noframes_duration, noframes_minutes_from_issues, hang_minutes_from_health


class RebootHistory:
    """
    Track reboot history with rate limiting and pause mechanism.
    
    Stores reboot timestamps in JSON file that persists across reboots.
    Implements rolling 1-hour window for rate limiting.
    """
    
    def __init__(self, history_file: str):
        """
        Initialize reboot history tracker.
        
        Args:
            history_file: Path to JSON file storing reboot history
        """
        self.history_file = Path(history_file)
        self.reboots: List[float] = []  # Unix timestamps
        self.pause_until: Optional[float] = None  # Unix timestamp
        self._load()
    
    def _load(self):
        """Load reboot history from file."""
        if not self.history_file.exists():
            return
        
        try:
            with open(self.history_file, 'r') as f:
                data = json.load(f)
                self.reboots = data.get('reboots', [])
                self.pause_until = data.get('pause_until')
        except Exception as e:
            log(f"[WATCHDOG REBOOT] Warning: Could not load reboot history: {e}", level="WARNING")
    
    def _save(self):
        """Save reboot history to file."""
        try:
            data = {
                'reboots': self.reboots,
                'pause_until': self.pause_until
            }
            with open(self.history_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log(f"[WATCHDOG REBOOT] Warning: Could not save reboot history: {e}", level="WARNING")
    
    def _cleanup_old_reboots(self):
        """Remove reboots older than 1 hour from history."""
        cutoff = time.time() - 3600  # 1 hour ago
        self.reboots = [r for r in self.reboots if r > cutoff]
    
    def record_reboot(self):
        """Record a new reboot timestamp."""
        self._cleanup_old_reboots()
        self.reboots.append(time.time())
        self._save()
    
    def get_reboots_in_last_hour(self) -> int:
        """
        Get count of reboots in last 60 minutes.
        
        Returns:
            Number of reboots in rolling 1-hour window
        """
        self._cleanup_old_reboots()
        return len(self.reboots)
    
    def is_paused(self) -> bool:
        """
        Check if reboot watchdog is currently paused.
        
        Returns:
            True if paused, False if active
        """
        if self.pause_until is None:
            return False
        
        if time.time() < self.pause_until:
            return True
        
        # Pause expired, clear it
        self.pause_until = None
        self._save()
        return False
    
    def set_pause(self, duration_hours: int):
        """
        Pause reboots for specified duration.
        
        Args:
            duration_hours: How many hours to pause
        """
        self.pause_until = time.time() + (duration_hours * 3600)
        self._save()
    
    def get_time_until_pause_ends(self) -> Optional[int]:
        """
        Get seconds until pause ends.
        
        Returns:
            Seconds remaining, or None if not paused
        """
        if not self.is_paused():
            return None
        
        # Type check: pause_until is guaranteed to exist here due to is_paused() check
        if self.pause_until is None:
            return None
            
        return int(self.pause_until - time.time())
    
    def get_last_reboot_time(self) -> Optional[float]:
        """
        Get timestamp of most recent reboot.
        
        Returns:
            Unix timestamp of last reboot, or None if no reboots
        """
        self._cleanup_old_reboots()
        if not self.reboots:
            return None
        return max(self.reboots)


class LocalHealthChecker:
    """Read capture health from local status file written by system_watchdog."""

    def __init__(self, status_path: str, max_age_seconds: int):
        self.status_path = status_path
        self.max_age_seconds = max_age_seconds

    def check(self) -> Dict:
        raw = read_local_health_status(self.status_path, self.max_age_seconds)

        if raw.get('available'):
            issues = raw.get('issues', [])
            hang_minutes = hang_minutes_from_health(raw)
            encode_only = raw.get('encode_only_soak', False)
            if encode_only:
                has_hang = hang_minutes > 0 or any('NoEncode:' in i for i in issues)
            else:
                has_hang = hang_minutes > 0 or any('NoFrames:' in i for i in issues)
            return {
                'available': True,
                'healthy': not has_hang,
                'noframes_minutes': hang_minutes,
                'encode_only_soak': encode_only,
                'issues': issues,
                'updated_at': raw.get('updated_at'),
            }

        if raw.get('stale'):
            issues = raw.get('issues', [])
            hang_minutes = hang_minutes_from_health(raw)
            encode_only = raw.get('encode_only_soak', False)
            if encode_only:
                has_hang = hang_minutes > 0 or any('NoEncode:' in i for i in issues)
            else:
                has_hang = hang_minutes > 0 or any('NoFrames:' in i for i in issues)
            return {
                'available': False,
                'stale': True,
                'healthy': not has_hang if has_hang else None,
                'noframes_minutes': hang_minutes,
                'encode_only_soak': encode_only,
                'issues': issues,
                'error': f"local health file stale ({int(raw.get('age_seconds', 0))}s old)",
            }

        return {
            'available': False,
            'missing': raw.get('missing', False),
            'healthy': None,
            'noframes_minutes': 0,
            'error': raw.get('error', 'local health file missing'),
        }


class CentralHealthChecker:
    """
    Optional central server health check for fleet visibility.

    Failures do NOT imply the camera is healthy — local check is authoritative.
    """

    def __init__(self, api_client: APIClient, camera_id: str):
        self.api_client = api_client
        self.camera_id = camera_id

    def check(self) -> Dict:
        """
        Query central server for recent NoFrames errors.

        Returns dict with noframes_minutes and error on failure (never assumes healthy).
        """
        try:
            one_hour_ago = datetime.now() - timedelta(hours=1)

            response = self.api_client.session.get(
                f"{self.api_client.base_url}/logs",
                params={
                    "source": self.camera_id,
                    "level": "ERROR",
                    "after": one_hour_ago.isoformat(),
                    "limit": 100
                }
            )

            if response.status_code != 200:
                return {
                    'available': False,
                    'noframes_minutes': 0,
                    'error': f"API returned {response.status_code}"
                }

            logs = response.json().get('logs', [])

            for log_entry in logs:
                message = log_entry.get('message', '')
                if 'NoFrames:' in message:
                    return {
                        'available': True,
                        'healthy': False,
                        'noframes_minutes': parse_noframes_duration(message),
                        'last_error_message': message,
                        'error': None
                    }

            return {
                'available': True,
                'healthy': True,
                'noframes_minutes': 0,
                'last_error_message': logs[0].get('message') if logs else None,
                'error': None
            }

        except Exception as e:
            return {
                'available': False,
                'noframes_minutes': 0,
                'error': str(e)
            }


class HealthChecker:
    """
    Local-first health checker for reboot decisions.

    Primary: local_health.json from system_watchdog
    Secondary: central server API (optional enrichment only)
    """

    AGENT_SERVICE = 'security-camera-agent.service'

    def __init__(self, local_checker: LocalHealthChecker, central_checker: CentralHealthChecker):
        self.local_checker = local_checker
        self.central_checker = central_checker

    def _is_agent_running(self) -> bool:
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', '--quiet', self.AGENT_SERVICE],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def check_health(self) -> Dict:
        local = self.local_checker.check()
        central = self.central_checker.check()
        central_error = central.get('error')
        sources = []

        if config.REBOOT_WATCHDOG_LOCAL_CHECK and local.get('available'):
            noframes_minutes = local['noframes_minutes']
            healthy = local['healthy']
            sources.append('local')
            if central_error:
                log(
                    f"[WATCHDOG REBOOT] Central API unreachable ({central_error}) — "
                    f"using local health (NoFrames: {noframes_minutes}m)",
                    level="WARNING"
                )
        elif config.REBOOT_WATCHDOG_LOCAL_CHECK:
            if not self._is_agent_running():
                return {
                    'skip_reboot': True,
                    'reason': 'agent not running (deploy/maintenance)',
                    'healthy': True,
                    'noframes_minutes': 0,
                    'sources': [],
                    'central_error': central_error,
                }

            if local.get('stale') and local.get('healthy') is False:
                noframes_minutes = local['noframes_minutes']
                healthy = local.get('healthy', False)
                sources.append('local-stale')
                log(
                    f"[WATCHDOG REBOOT] Local health file stale — "
                    f"using last known NoFrames: {noframes_minutes}m",
                    level="WARNING"
                )
            elif not central.get('error'):
                noframes_minutes = central['noframes_minutes']
                healthy = central['healthy']
                sources.append('central')
                log("[WATCHDOG REBOOT] Local health unavailable — using central API", level="WARNING")
            else:
                log(
                    f"[WATCHDOG REBOOT] No health data (local: {local.get('error')}, "
                    f"central: {central_error}) — skipping reboot check",
                    level="WARNING"
                )
                return {
                    'skip_reboot': True,
                    'reason': 'no health data available',
                    'healthy': True,
                    'noframes_minutes': 0,
                    'sources': [],
                    'central_error': central_error,
                }
        else:
            if central.get('error'):
                log(f"[WATCHDOG REBOOT] Central API error: {central_error}", level="WARNING")
                return {
                    'skip_reboot': True,
                    'reason': 'central API unavailable',
                    'healthy': True,
                    'noframes_minutes': 0,
                    'sources': [],
                    'central_error': central_error,
                }
            noframes_minutes = central['noframes_minutes']
            healthy = central['healthy']
            sources.append('central')

        return {
            'healthy': healthy,
            'noframes_minutes': noframes_minutes,
            'sources': sources,
            'central_error': central_error,
        }


class StreamingChecker:
    """
    Check if camera is currently streaming video.
    
    Queries local Camera Control API endpoint.
    """
    
    def __init__(self, api_base: str):
        """
        Initialize streaming checker.
        
        Args:
            api_base: Base URL for Camera Control API (e.g., http://localhost:5000)
        """
        self.api_base = api_base
    
    def is_streaming(self) -> bool:
        """
        Check if camera is currently streaming.
        
        Returns:
            True if streaming, False otherwise
        """
        try:
            import requests
            response = requests.get(f"{self.api_base}/streaming/status", timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                return data.get('streaming', False)
            
            return False
            
        except Exception as e:
            log(f"[WATCHDOG REBOOT] Warning: Could not check streaming status: {e}", level="WARNING")
            return False  # Assume not streaming on error


class RebootWatchdog:
    """
    Main watchdog service that monitors camera and executes reboots.
    
    Coordinates health checking, safety limits, and reboot execution.
    """
    
    def __init__(self):
        """Initialize reboot watchdog service."""
        self.camera_id = config.CAMERA_ID
        
        # Initialize API client (uses config internally)
        self.api_client = APIClient()
        
        # Initialize logger (creates own APIClient internally, just needs log_dir)
        self.logger = EnhancedLogger()
        
        # Initialize components
        self.history = RebootHistory(config.REBOOT_WATCHDOG_HISTORY_FILE)
        self.health_checker = HealthChecker(
            LocalHealthChecker(
                config.LOCAL_HEALTH_STATUS_FILE,
                config.LOCAL_HEALTH_MAX_AGE_SECONDS,
            ),
            CentralHealthChecker(self.api_client, self.camera_id),
        )
        self.streaming_checker = StreamingChecker(config.CAMERA_CONTROL_API_BASE)
        
        log(f"[WATCHDOG REBOOT] Watchdog service initialized for {self.camera_id}", level="INFO")
    
    def is_manually_disabled(self) -> bool:
        """
        Check if auto-reboot is manually disabled via flag file.
        
        Returns:
            True if disabled, False if enabled
        """
        return Path(config.REBOOT_WATCHDOG_DISABLE_FLAG).exists()
    
    def run(self):
        """
        Main watchdog loop.
        
        Runs continuously, checking camera health every CHECK_INTERVAL seconds.
        """
        log(f"[WATCHDOG REBOOT] Starting watchdog service", level="INFO")
        log(f"[WATCHDOG REBOOT] Configuration: hang_threshold={config.REBOOT_WATCHDOG_HANG_THRESHOLD}m, "
            f"check_interval={config.REBOOT_WATCHDOG_CHECK_INTERVAL}s, "
            f"cooldown={config.REBOOT_WATCHDOG_COOLDOWN}s, "
            f"local_check={config.REBOOT_WATCHDOG_LOCAL_CHECK}", level="INFO")
        
        while True:
            try:
                self._check_cycle()
            except Exception as e:
                log(f"[WATCHDOG REBOOT] Error in check cycle: {e}", level="ERROR")
            
            # Wait until next check
            time.sleep(config.REBOOT_WATCHDOG_CHECK_INTERVAL)
    
    def _check_cycle(self):
        """
        Execute one check cycle.
        
        Checks camera health and decides whether to reboot.
        """
        # Check if watchdog is enabled
        if not config.REBOOT_WATCHDOG_ENABLED:
            return
        
        # Check if manually disabled
        if self.is_manually_disabled():
            log(f"[WATCHDOG REBOOT] Auto-reboot manually disabled (flag file exists)", level="WARNING")
            return
        
        # Check if paused
        if self.history.is_paused():
            remaining = self.history.get_time_until_pause_ends()
            
            # Type assertion: remaining is guaranteed to be int here due to is_paused() check
            if remaining is not None:
                hours = remaining // 3600
                minutes = (remaining % 3600) // 60
                log(f"[WATCHDOG REBOOT] Reboot watchdog paused (resume in {hours}h {minutes}m)", level="WARNING")
            else:
                log(f"[WATCHDOG REBOOT] Reboot watchdog paused", level="WARNING")
            return
        
        # Check camera health (local-first)
        health = self.health_checker.check_health()

        if health.get('skip_reboot'):
            log(f"[WATCHDOG REBOOT] Skipping reboot check: {health.get('reason')}", level="INFO")
            return

        noframes_minutes = health['noframes_minutes']
        source = ', '.join(health.get('sources', [])) or 'unknown'
        
        # Camera is healthy
        if health['healthy']:
            log(f"[WATCHDOG REBOOT] Camera is healthy, no reboot needed "
                f"(NoFrames: {noframes_minutes}m, source: {source})",
                level="INFO")
            return

        # Camera is struggling but not yet at threshold
        if noframes_minutes < config.REBOOT_WATCHDOG_HANG_THRESHOLD:
            log(f"[WATCHDOG REBOOT] Camera struggling (NoFrames: {noframes_minutes}m, "
                f"threshold: {config.REBOOT_WATCHDOG_HANG_THRESHOLD}m, source: {source})",
                level="WARNING")
            return

        # Camera is hung - proceed to reboot decision
        log(f"[WATCHDOG REBOOT] Camera is UNHEALTHY (NoFrames: {noframes_minutes}m, source: {source}) - "
            f"evaluating reboot decision", level="ERROR")
        
        # Safety Check #1: Is camera streaming?
        if config.REBOOT_WATCHDOG_CHECK_STREAMING:
            if self.streaming_checker.is_streaming():
                log(f"[WATCHDOG REBOOT] Camera is UNHEALTHY but STREAMING - reboot skipped "
                    f"(NoFrames: {noframes_minutes}m)", level="WARNING")
                return
        
        # Safety Check #2: Reboot cooldown
        last_reboot = self.history.get_last_reboot_time()
        if last_reboot:
            time_since_reboot = int(time.time() - last_reboot)
            if time_since_reboot < config.REBOOT_WATCHDOG_COOLDOWN:
                minutes_ago = time_since_reboot // 60
                cooldown_minutes = config.REBOOT_WATCHDOG_COOLDOWN // 60
                log(f"[WATCHDOG REBOOT] Reboot SKIPPED - cooldown active "
                    f"(last reboot: {minutes_ago}m ago, minimum: {cooldown_minutes}m)", level="WARNING")
                return
        
        # Safety Check #3: Rate limit
        reboots_last_hour = self.history.get_reboots_in_last_hour()
        if reboots_last_hour >= config.REBOOT_WATCHDOG_MAX_REBOOTS_PER_HOUR:
            # Hit rate limit - pause for 24 hours
            self.history.set_pause(config.REBOOT_WATCHDOG_PAUSE_DURATION)
            log(f"[WATCHDOG REBOOT] CRITICAL - Too many reboots ({reboots_last_hour} in 1 hour), "
                f"paused REBOOTS for {config.REBOOT_WATCHDOG_PAUSE_DURATION}h", level="WARNING")
            return
        
        # All safety checks passed - execute reboot
        self._execute_reboot(noframes_minutes, reboots_last_hour)
    
    def _execute_reboot(self, noframes_minutes: int, reboots_in_hour: int):
        """
        Execute system reboot.
        
        Args:
            noframes_minutes: Duration of NoFrames condition
            reboots_in_hour: Number of reboots in last hour
        """
        # Log reboot decision
        log(f"[WATCHDOG REBOOT] Camera is UNHEALTHY, REBOOT IS NEEDED - Reboot initiated "
            f"(NoFrames: {noframes_minutes}m, reboots_1h: {reboots_in_hour})", level="ERROR")
        
        # Record reboot in history
        self.history.record_reboot()
        
        # Grace period - allow logs to flush
        log(f"[WATCHDOG REBOOT] Waiting {config.REBOOT_WATCHDOG_PRE_REBOOT_DELAY}s grace period "
            f"before reboot...", level="INFO")
        time.sleep(config.REBOOT_WATCHDOG_PRE_REBOOT_DELAY)
        
        # Execute reboot
        log(f"[WATCHDOG REBOOT] Executing system reboot NOW", level="ERROR")
        
        try:
            subprocess.run(['/sbin/reboot'], check=True)
        except Exception as e:
            log(f"[WATCHDOG REBOOT] FAILED to execute reboot: {e}", level="ERROR")


def main():
    """
    Main entry point for reboot watchdog service.
    """
    print("="*60)
    print("Security Camera System - Reboot Watchdog Service")
    print("="*60)
    print(f"Camera ID: {config.CAMERA_ID}")
    print(f"Central Server: {config.CENTRAL_SERVER_HOST}:{config.CENTRAL_SERVER_PORT}")
    print(f"Check Interval: {config.REBOOT_WATCHDOG_CHECK_INTERVAL}s")
    print(f"Hang Threshold: {config.REBOOT_WATCHDOG_HANG_THRESHOLD}m")
    print("="*60)
    
    # Initialize and run watchdog
    watchdog = RebootWatchdog()
    watchdog.run()


if __name__ == "__main__":
    main()