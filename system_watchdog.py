"""
Security Camera System - Comprehensive Watchdog
===============================================
Monitors system health including:
- Thread liveness and activity
- Hardware health (camera, disk, memory)
- Component activity (motion checks, transfers, etc.)
- 24-hour statistics

Logs status periodically and raises errors when issues detected.
"""

import threading
import time
import subprocess
import shutil
import psutil
import os
from datetime import datetime, timedelta
from logger import log
from config import config


class SystemWatchdog:
    """
    Comprehensive system health monitor.
    
    Monitors all threads, hardware, and activity.
    Logs regular health reports and alerts on issues.
    
    Usage:
        watchdog = SystemWatchdog(
            circular_buffer=buffer,
            motion_detector=detector,
            event_processor=processor,
            transfer_manager=manager,
            api_client=client
        )
        watchdog.start()
        # ... system runs ...
        watchdog.stop()
    """
    
    def __init__(self, circular_buffer, motion_detector, event_processor, 
                 transfer_manager, api_client):
        """
        Initialize watchdog with references to all components.
        
        Args:
            circular_buffer: CircularBuffer instance
            motion_detector: MotionDetector instance
            event_processor: EventProcessor instance
            transfer_manager: TransferManager instance
            api_client: APIClient instance
        """
        self.circular_buffer = circular_buffer
        self.motion_detector = motion_detector
        self.event_processor = event_processor
        self.transfer_manager = transfer_manager
        self.api_client = api_client
        
        # Watchdog control
        self.running = False
        self.thread = None
        
        # Monitoring intervals
        self.quick_check_interval = 60  # Quick health check every 60s
        self.detailed_report_interval = 300  # Detailed report every 5 minutes
        
        # Last check values (for detecting stuck threads)
        self.last_frame_count = 0
        self.last_motion_checks = 0
        self.last_files_transferred = 0
        
        # Last check time
        self.last_quick_check = 0
        self.last_detailed_report = 0
        
        log("SystemWatchdog initialized")
    
    def start(self):
        """Start watchdog monitoring thread."""
        self.running = True
        self.thread = threading.Thread(
            target=self._watchdog_loop,
            name="SystemWatchdog",
            daemon=True
        )
        self.thread.start()
        log("SystemWatchdog started")
    
    def stop(self):
        """Stop watchdog monitoring."""
        log("Stopping SystemWatchdog...")
        self.running = False
        
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5.0)
        
        log("SystemWatchdog stopped")
    
    def _watchdog_loop(self):
        """Main watchdog monitoring loop."""
        log("SystemWatchdog monitoring loop started")
        
        while self.running:
            try:
                current_time = time.time()
                
                # Quick health check every 60s
                if current_time - self.last_quick_check >= self.quick_check_interval:
                    self._quick_health_check()
                    self.last_quick_check = current_time
                
                # Detailed report every 5 minutes
                if current_time - self.last_detailed_report >= self.detailed_report_interval:
                    self._detailed_health_report()
                    self.last_detailed_report = current_time
                
                # Sleep before next check
                time.sleep(10)  # Check every 10s to hit intervals accurately
                
            except Exception as e:
                if self.running:
                    log(f"Error in watchdog loop: {e}", level="ERROR")
                    time.sleep(30)
    
    def _quick_health_check(self):
        """
        Quick health check - one-line summary.
        Logs INFO if healthy, ERROR if issues detected.
        """
        issues = []
        
        # Check threads
        thread_status = self._check_threads()
        total_threads = len(thread_status)
        alive_threads = sum(1 for t in thread_status.values() if t['alive'])
        
        if alive_threads < total_threads:
            issues.append(f"Threads:{alive_threads}/{total_threads}")
        
        # Check activity
        activity_issues = self._check_activity()
        if activity_issues:
            issues.extend(activity_issues)
        
        # Check hardware
        hardware_status = self._check_hardware()
        if not hardware_status['camera_ok']:
            issues.append("Camera:NOT_DETECTED")
        if not hardware_status['disk_ok']:
            issues.append(f"Disk:{hardware_status['disk_free_gb']:.1f}GB")
        if not hardware_status['memory_ok']:
            issues.append(f"Mem:{hardware_status['memory_percent']}%")
        
        # Log result
        if issues:
            log(f"Watchdog: ⚠ ISSUES DETECTED: {', '.join(issues)}", level="ERROR")
        else:
            # Calculate activity rates
            motion_rate = self.motion_detector.check_count - self.last_motion_checks
            
            log(f"Watchdog: ✓ All healthy | T:{alive_threads}/{total_threads} | "
                f"Checks:{motion_rate}/60s | "
                f"Disk:{hardware_status['disk_free_gb']:.0f}GB | "
                f"Mem:{hardware_status['memory_percent']}%", 
                level="INFO")
    
    def _detailed_health_report(self):
        """
        Detailed health report every 5 minutes.
        Provides comprehensive system status.
        """
        log("="*60, level="INFO")
        log("System Health Report", level="INFO")
        log("="*60, level="INFO")
        
        # Thread health
        thread_status = self._check_threads()
        alive_count = sum(1 for t in thread_status.values() if t['alive'])
        log(f"Threads: {alive_count}/{len(thread_status)} alive", level="INFO")
        
        for name, status in thread_status.items():
            if status['alive']:
                details = status.get('details', '')
                log(f"  ✓ {name}: {details}", level="INFO")
            else:
                log(f"  ✗ {name}: DEAD", level="ERROR")
        
        # Activity summary
        log("Activity (last 5 minutes):", level="INFO")
        motion_checks = self.motion_detector.check_count - self.last_motion_checks
        files_transferred = self.transfer_manager.files_transferred - self.last_files_transferred
        
        log(f"  Motion checks: {motion_checks}", level="INFO")
        log(f"  Files transferred: {files_transferred}", level="INFO")
        
        # Update last values
        self.last_motion_checks = self.motion_detector.check_count
        self.last_files_transferred = self.transfer_manager.files_transferred
        
        # Hardware health
        hardware = self._check_hardware()
        log("Hardware:", level="INFO")
        log(f"  Camera: {'✓ OK' if hardware['camera_ok'] else '✗ NOT DETECTED'}", 
            level="INFO" if hardware['camera_ok'] else "ERROR")
        log(f"  Disk: {hardware['disk_free_gb']:.1f}GB free ({hardware['disk_percent_free']}% available)",
            level="INFO" if hardware['disk_ok'] else "WARNING")
        log(f"  Memory: {hardware['memory_mb']:.0f}MB / {hardware['memory_total_mb']:.0f}MB "
            f"({hardware['memory_percent']}% used)",
            level="INFO" if hardware['memory_ok'] else "WARNING")
        
        # 24-hour statistics
        stats = self._get_24h_stats()
        if stats:
            log("Last 24 hours:", level="INFO")
            log(f"  Events: {stats.get('events', 0)}", level="INFO")
            log(f"  Files: {stats.get('files', 0)}", level="INFO")
            log(f"  Data: {stats.get('bytes', 0)/(1024*1024):.1f}MB", level="INFO")
        
        log("="*60, level="INFO")
    
    def _check_threads(self):
        """
        Check all thread statuses.
        
        Returns:
            dict: Thread name -> {alive: bool, details: str}
        """
        status = {}
        
        # CircularBuffer
        cb_health = self.circular_buffer.get_health()
        time_since_frame = time.time() - cb_health['last_frame_time'] if cb_health['last_frame_time'] else 999
        status['CircularBufferCapture'] = {
            'alive': cb_health['thread_alive'],
            'details': f"last frame {time_since_frame:.1f}s ago" if time_since_frame < 60 else "STALE FRAMES"
        }
        
        # MotionDetector
        md_health = self.motion_detector.get_health()
        motion_delta = self.motion_detector.check_count - self.last_motion_checks
        if md_health['in_cooldown']:
            details = f"in cooldown"
        elif md_health['paused']:
            details = f"paused"
        else:
            details = f"{motion_delta} checks since last report"
        status['MotionDetector'] = {
            'alive': md_health['thread_alive'],
            'details': details
        }
        
        # EventProcessor
        ep_health = self.event_processor.get_health()
        if ep_health['is_processing']:
            details = f"processing event"
        else:
            details = f"waiting for events"
        status['EventProcessor'] = {
            'alive': ep_health['thread_alive'],
            'details': details
        }
        
        # TransferManager
        tm_health = self.transfer_manager.get_health()
        files_delta = self.transfer_manager.files_transferred - self.last_files_transferred
        status['TransferManager'] = {
            'alive': tm_health['thread_alive'],
            'details': f"{files_delta} files since last report"
        }
        
        # LogWriter (get from logger if accessible)
        # For now, assume it's running if we can log
        status['LogWriter'] = {
            'alive': True,
            'details': "batching logs"
        }
        
        return status
    
    def _check_activity(self):
        """
        Check for stuck/inactive components.
        
        Returns:
            list: List of activity issues (empty if all OK)
        """
        issues = []
        
        # Check frame capture
        cb_health = self.circular_buffer.get_health()
        if cb_health['last_frame_time']:
            time_since_frame = time.time() - cb_health['last_frame_time']
            if time_since_frame > 60:  # No frames for 60s
                # Format time in human-readable format
                if time_since_frame < 3600:  # Less than 1 hour
                    formatted_time = f"{int(time_since_frame/60)}m"
                elif time_since_frame < 86400:  # Less than 1 day
                    hours = int(time_since_frame/3600)
                    minutes = int((time_since_frame % 3600)/60)
                    formatted_time = f"{hours}h{minutes}m"
                else:  # 1+ days
                    days = int(time_since_frame/86400)
                    hours = int((time_since_frame % 86400)/3600)
                    formatted_time = f"{days}d{hours}h"
                
                issues.append(f"NoFrames:{formatted_time}")
        
        # Check motion detector
        current_checks = self.motion_detector.check_count
        if current_checks == self.last_motion_checks:
            # Only flag if NOT in cooldown or paused
            md_health = self.motion_detector.get_health()
            if not md_health['in_cooldown'] and not md_health['paused']:
                issues.append("MotionStuck")
        
        return issues
    
    def _check_hardware(self):
        """
        Check hardware health (camera, disk, memory).
        
        Returns:
            dict: Hardware status
        """
        # Check camera detection
        camera_ok = self._check_camera_detected()
        
        # Check disk space
        disk_stat = shutil.disk_usage(config.BASE_PATH)
        disk_free_gb = disk_stat.free / (1024**3)
        disk_total_gb = disk_stat.total / (1024**3)
        disk_percent_free = int((disk_free_gb / disk_total_gb) * 100)
        disk_ok = disk_free_gb > 1.0  # At least 1GB free
        
        # Check memory
        memory = psutil.virtual_memory()
        memory_mb = memory.used / (1024**2)
        memory_total_mb = memory.total / (1024**2)
        memory_percent = memory.percent
        memory_ok = memory_percent < 80  # Less than 80% used
        
        return {
            'camera_ok': camera_ok,
            'disk_free_gb': disk_free_gb,
            'disk_total_gb': disk_total_gb,
            'disk_percent_free': disk_percent_free,
            'disk_ok': disk_ok,
            'memory_mb': memory_mb,
            'memory_total_mb': memory_total_mb,
            'memory_percent': int(memory_percent),
            'memory_ok': memory_ok
        }
    
    def _check_camera_detected(self):
        """
        Check if camera is detected by system on Trixie.

        Uses: rpicam-hello --list-cameras
        Returns True if any known sensor name appears in the output,
        or if at least one numbered camera line is present.
        """
        try:
            result = subprocess.run(
                ['rpicam-hello', '--list-cameras'],
                capture_output=True,
                text=True,
                timeout=5
            )

            # If the command itself failed, treat as not detected
            if result.returncode != 0:
                return False

            out = result.stdout.lower()

            # 1) Fast path: known sensors in your fleet
            #    Examples from your nodes:
            #    - "0 : ov5647 [2592x1944 10-bit gbrg] ..."
            #    - "0 : imx708 [4608x2592 10-bit rggb] ..."
            #    - "0 : imx708_wide [4608x2592 10-bit] ..."
            known_sensors = ('ov5647', 'imx708', 'imx708_wide')
            if any(sensor in out for sensor in known_sensors):
                return True

            # 2) Fallback: generic "numbered camera line" detection
            #    Look for something like "0 : <name> [...]"
            for line in out.splitlines():
                line = line.strip()
                if line and line[0].isdigit() and " :" in line:
                    return True

            # If we got here, no camera lines were found
            return False

        except (subprocess.TimeoutExpired, FileNotFoundError):
            # rpicam-hello missing or hung: treat as not detected
            return False

    
    def _get_24h_stats(self):
        """
        Get 24-hour statistics from central server.
        
        Returns:
            dict: Stats or None if unavailable
        """
        try:
            return self.api_client.get_camera_stats(hours=24)
        except Exception as e:
            log(f"Failed to get 24h stats: {e}", level="WARNING")
            return None


# ============================================================================
# STANDALONE TESTING
# ============================================================================

if __name__ == "__main__":
    """
    Test watchdog functionality.
    Requires running system components.
    """
    print("SystemWatchdog test requires running camera system.")
    print("Import and use in sec_cam_main.py instead.")
