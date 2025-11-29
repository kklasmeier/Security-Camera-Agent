#!/usr/bin/env python3
"""
Security Camera System - Reboot Watchdog Service
=================================================
Monitors camera health and automatically reboots on hang detection.

This service runs as root (separate from camera agent) and:
- Queries central server for camera health status
- Detects NoFrames errors lasting > 60 minutes
- Checks safety limits before rebooting
- Logs all actions to central server
- Implements rate limiting and pause mechanism

Design:
- Runs independently from camera agent (separate systemd service)
- Uses shared camera agent modules (logger.py, api_client.py, config.py)
- Executes as root to enable system reboot
- Monitors single camera (one instance per camera Pi)

Safety Mechanisms:
- 5-minute cooldown between reboots
- 5 reboots/hour limit triggers 24-hour pause
- Skips reboot if camera is streaming
- Manual disable via flag file
- Comprehensive logging of all decisions
"""

import sys
import time
import json
import re
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List

# Add camera agent directory to Python path
CAMERA_AGENT_DIR = Path("/home/pi/Security-Camera-Agent")
sys.path.insert(0, str(CAMERA_AGENT_DIR))

# Import from camera agent modules (shared code)
from logger import EnhancedLogger, log
from api_client import APIClient
from config import config


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


class CameraHealthChecker:
    """
    Check camera health by querying central server logs.
    
    Looks for NoFrames errors and parses duration to determine if camera is hung.
    """
    
    def __init__(self, api_client: APIClient, camera_id: str):
        """
        Initialize health checker.
        
        Args:
            api_client: API client for central server
            camera_id: Camera identifier
        """
        self.api_client = api_client
        self.camera_id = camera_id
    
    def check_health(self) -> Dict:
        """
        Check camera health by querying recent error logs.
        
        Returns:
            Dict with keys:
                - healthy: bool (True if camera OK, False if hung)
                - noframes_minutes: int (duration of NoFrames, 0 if healthy)
                - last_error_message: str (most recent error, or None)
                - error: str (error message if check failed)
        """
        try:
            # Query last hour of ERROR logs from this camera
            one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
            
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
                    'healthy': True,  # Assume healthy if can't check
                    'noframes_minutes': 0,
                    'last_error_message': None,
                    'error': f"API returned {response.status_code}"
                }
            
            data = response.json()
            logs = data.get('logs', [])
            
            if not logs:
                # No errors in last hour - camera is healthy
                return {
                    'healthy': True,
                    'noframes_minutes': 0,
                    'last_error_message': None,
                    'error': None
                }
            
            # Look for most recent NoFrames error
            for log_entry in logs:
                message = log_entry.get('message', '')
                
                # Check for NoFrames in watchdog messages
                if 'NoFrames:' in message:
                    # Parse duration: "NoFrames:65m" or "NoFrames:1h30m" or "NoFrames:2d5h15m"
                    duration_minutes = self._parse_noframes_duration(message)
                    
                    return {
                        'healthy': False,
                        'noframes_minutes': duration_minutes,
                        'last_error_message': message,
                        'error': None
                    }
            
            # Errors exist but no NoFrames - camera is healthy
            return {
                'healthy': True,
                'noframes_minutes': 0,
                'last_error_message': logs[0].get('message'),
                'error': None
            }
            
        except Exception as e:
            log(f"[WATCHDOG REBOOT] Error checking camera health: {e}", level="ERROR")
            return {
                'healthy': True,  # Assume healthy on error
                'noframes_minutes': 0,
                'last_error_message': None,
                'error': str(e)
            }
    
    def _parse_noframes_duration(self, message: str) -> int:
        """
        Parse NoFrames duration from log message.
        
        Args:
            message: Log message containing "NoFrames:Xm" or "NoFrames:XhYm" etc.
        
        Returns:
            Duration in minutes
        """
        # Match patterns: "65m", "1h30m", "2d5h15m"
        match = re.search(r'NoFrames:(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?', message)
        
        if not match:
            return 0
        
        days = int(match.group(1) or 0)
        hours = int(match.group(2) or 0)
        minutes = int(match.group(3) or 0)
        
        total_minutes = (days * 24 * 60) + (hours * 60) + minutes
        return total_minutes


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
        self.health_checker = CameraHealthChecker(self.api_client, self.camera_id)
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
            f"cooldown={config.REBOOT_WATCHDOG_COOLDOWN}s", level="INFO")
        
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
        
        # Check camera health
        health = self.health_checker.check_health()
        
        if health.get('error'):
            log(f"[WATCHDOG REBOOT] Health check error: {health['error']}", level="WARNING")
            return
        
        noframes_minutes = health['noframes_minutes']
        
        # Camera is healthy
        if health['healthy']:
            log(f"[WATCHDOG REBOOT] Camera is healthy, no reboot needed (NoFrames: {noframes_minutes}m)", 
                level="INFO")
            return
        
        # Camera is struggling but not yet at threshold
        if noframes_minutes < config.REBOOT_WATCHDOG_HANG_THRESHOLD:
            log(f"[WATCHDOG REBOOT] Camera struggling (NoFrames: {noframes_minutes}m, "
                f"threshold: {config.REBOOT_WATCHDOG_HANG_THRESHOLD}m)", level="WARNING")
            return
        
        # Camera is hung - proceed to reboot decision
        log(f"[WATCHDOG REBOOT] Camera is UNHEALTHY (NoFrames: {noframes_minutes}m) - "
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
            subprocess.run(['reboot'], check=True)
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