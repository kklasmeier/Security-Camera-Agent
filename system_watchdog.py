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
from local_health import write_local_health_status


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
        
        # Freeze detection tracking (prevent diagnostic spam)
        self.last_freeze_diagnostic_time = 0
        self.freeze_diagnostic_interval = 300  # Only capture diagnostics every 5 minutes
        
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

        self._write_local_health_status(issues, thread_status)
    
    def _write_local_health_status(self, issues, thread_status):
        """Write local health JSON for reboot watchdog (local-first hang detection)."""
        cb_health = self.circular_buffer.get_health()
        last_frame_time = cb_health.get('last_frame_time')
        noframes_seconds = 0
        if last_frame_time:
            noframes_seconds = max(0, int(time.time() - last_frame_time))

        alive_threads = sum(1 for t in thread_status.values() if t['alive'])
        total_threads = len(thread_status)

        status = {
            'updated_at': datetime.now().isoformat(),
            'updated_at_unix': time.time(),
            'camera_id': config.CAMERA_ID,
            'healthy': len(issues) == 0,
            'issues': issues,
            'noframes_seconds': noframes_seconds,
            'noframes_minutes': noframes_seconds // 60,
            'last_frame_time': last_frame_time,
            'frame_count': cb_health.get('frame_count', 0),
            'threads_alive': alive_threads,
            'threads_total': total_threads,
        }

        try:
            write_local_health_status(config.LOCAL_HEALTH_STATUS_FILE, status)
        except Exception as e:
            log(f"Watchdog: Failed to write local health status: {e}", level="WARNING")
    
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
        
        # CircularBuffer - Enhanced with state detection
        cb_health = self.circular_buffer.get_health()
        time_since_frame = time.time() - cb_health['last_frame_time'] if cb_health['last_frame_time'] else 999
        
        # Check for hung thread
        thread_state = cb_health.get('thread_state', 'UNKNOWN')
        time_in_state = cb_health.get('time_in_current_state', 0)
        time_since_success = cb_health.get('time_since_successful_capture', 999)
        
        # Detect hung states
        if thread_state == "CALLING_CAPTURE_ARRAY" and time_in_state > 5.0:
            details = f"🚨 HUNG in capture_array() for {time_in_state:.1f}s!"
        elif thread_state == "SLEEPING" and time_in_state > 10.0:
            details = f"⚠️  Stuck sleeping for {time_in_state:.1f}s"
        elif time_since_frame < 60:
            details = f"last frame {time_since_frame:.1f}s ago, state: {thread_state}"
        else:
            details = f"STALE FRAMES ({time_since_frame/60:.0f}m), state: {thread_state}, in_state: {time_in_state:.1f}s"
        
        status['CircularBufferCapture'] = {
            'alive': cb_health['thread_alive'],
            'details': details
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
        
        # ===================================================================
        # ENHANCED FRAME CAPTURE CHECKING WITH FREEZE DETECTION
        # ===================================================================
        cb_health = self.circular_buffer.get_health()
        
        # Check 1: Are we getting captures at all?
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
        
        # Check 2: Are frames actually changing? (Freeze detection)
        frame_health = self._check_frame_freeze(cb_health)
        if frame_health['frozen']:
            # Get motion detector state to provide context
            md_health = self.motion_detector.get_health()
            
            # Only report as frozen if motion detector should be running
            # (Not paused for streaming, not in cooldown)
            if not md_health['paused'] and not md_health['in_cooldown']:
                reason = frame_health['reason']
                duration = frame_health.get('duration', 0)
                
                if duration < 60:
                    duration_str = f"{int(duration)}s"
                elif duration < 3600:
                    duration_str = f"{int(duration/60)}m"
                else:
                    hours = int(duration/3600)
                    minutes = int((duration % 3600)/60)
                    duration_str = f"{hours}h{minutes}m"
                
                # Add issue with frozen indicator
                if reason == 'identical_frames':
                    unique = frame_health.get('unique_in_last_10', 0)
                    issues.append(f"FrozenFrames:{duration_str}(unique:{unique}/10)")
                    
                    # Trigger comprehensive diagnostics (rate-limited)
                    current_time = time.time()
                    if current_time - self.last_freeze_diagnostic_time > self.freeze_diagnostic_interval:
                        self._capture_camera_diagnostics()
                        self.last_freeze_diagnostic_time = current_time
                else:
                    issues.append(f"CameraFrozen:{reason}")
            else:
                # Provide context in debug logging
                if md_health['paused']:
                    log(f"[FREEZE DEBUG] Identical frames detected but motion detector is PAUSED (streaming mode)", level="DEBUG")
                elif md_health['in_cooldown']:
                    log(f"[FREEZE DEBUG] Identical frames detected but motion detector is in COOLDOWN", level="DEBUG")
        # ===================================================================
        
        # Check motion detector
        current_checks = self.motion_detector.check_count
        if current_checks == self.last_motion_checks:
            # Only flag if NOT in cooldown or paused
            md_health = self.motion_detector.get_health()
            if not md_health['in_cooldown'] and not md_health['paused']:
                issues.append("MotionStuck")
        
        return issues
    
    def _check_frame_freeze(self, cb_health):
        """
        Check if frames are actually changing or frozen.
        
        Uses frame hash history to detect if camera is stuck on same image.
        
        Args:
            cb_health: Health dict from circular_buffer.get_health()
        
        Returns:
            dict: {
                'frozen': bool,
                'reason': str,  # 'identical_frames', 'no_captures', or None
                'duration': float,  # Seconds frozen
                'frozen_hash': str,  # Hash of frozen frame
                'unique_in_last_10': int  # Unique hashes in last 10 frames
            }
        """
        # Check if we have hash data
        if 'frame_hash_history' not in cb_health or not cb_health['frame_hash_history']:
            return {'frozen': False}
        
        hashes = cb_health['frame_hash_history']
        timestamps = cb_health.get('frame_hash_timestamps', [])
        
        # Need at least 10 frames to make a determination
        if len(hashes) < 10:
            return {'frozen': False}
        
        # Check last 10 frames for uniqueness
        last_10_hashes = hashes[-10:]
        unique_hashes = len(set(last_10_hashes))
        
        # If only 1 unique hash in last 10 frames = FROZEN
        if unique_hashes == 1:
            # Calculate how long we've been frozen
            if timestamps and len(timestamps) >= 10:
                freeze_duration = timestamps[-1] - timestamps[-10]
            else:
                freeze_duration = 0
            
            return {
                'frozen': True,
                'reason': 'identical_frames',
                'duration': freeze_duration,
                'frozen_hash': last_10_hashes[0],
                'unique_in_last_10': unique_hashes
            }
        
        # If very few unique hashes (2-3), might be starting to freeze
        # Log this but don't report as frozen yet
        if unique_hashes <= 3:
            log(f"[FREEZE DEBUG] Low frame diversity: {unique_hashes}/10 unique hashes in recent frames", 
                level="DEBUG")
        
        return {'frozen': False}

    def _capture_camera_diagnostics(self):
        """
        Capture comprehensive camera diagnostics when freeze is detected.
        
        This provides detailed state information to help diagnose hardware
        vs. software issues causing camera freezes.
        """
        log("="*60, level="ERROR")
        log("🚨 CAMERA FREEZE DETECTED - CAPTURING DIAGNOSTICS", level="ERROR")
        log("="*60, level="ERROR")
        
        try:
            # 1. Circular Buffer State
            cb_health = self.circular_buffer.get_health()
            log("Circular Buffer State:", level="ERROR")
            log(f"  Thread alive: {cb_health['thread_alive']}", level="ERROR")
            log(f"  Camera initialized: {cb_health['camera_initialized']}", level="ERROR")
            log(f"  Total frames captured: {cb_health['frame_count']}", level="ERROR")
            log(f"  Last frame time: {time.time() - cb_health['last_frame_time']:.1f}s ago", level="ERROR")
            
            # NEW: Thread state diagnostics
            thread_state = cb_health.get('thread_state', 'UNKNOWN')
            time_in_state = cb_health.get('time_in_current_state', 0)
            time_since_success = cb_health.get('time_since_successful_capture', 0)
            
            log("Thread State Diagnostics:", level="ERROR")
            log(f"  Current state: {thread_state}", level="ERROR")
            log(f"  Time in current state: {time_in_state:.1f}s", level="ERROR")
            log(f"  Time since successful capture: {time_since_success:.1f}s", level="ERROR")
            
            # Interpret state
            if thread_state == "CALLING_CAPTURE_ARRAY" and time_in_state > 5.0:
                log(f"  🚨 CRITICAL: Thread HUNG in capture_array() call!", level="ERROR")
                log(f"  🚨 This indicates Picamera2/camera driver is unresponsive", level="ERROR")
            elif thread_state == "SLEEPING" and time_in_state > 10.0:
                log(f"  ⚠️  Thread stuck in sleep loop (may be normal)", level="ERROR")
            elif time_since_success > 60:
                log(f"  ⚠️  No successful captures for {time_since_success/60:.1f} minutes", level="ERROR")
            
            # 2. Frame Hash Analysis
            if 'frame_hash_history' in cb_health:
                hashes = cb_health['frame_hash_history']
                timestamps = cb_health.get('frame_hash_timestamps', [])
                
                log("Frame Hash Analysis:", level="ERROR")
                log(f"  Total hashes tracked: {len(hashes)}", level="ERROR")
                log(f"  Unique hashes in history: {len(set(hashes))}", level="ERROR")
                
                if len(hashes) >= 10:
                    last_10 = hashes[-10:]
                    unique_last_10 = len(set(last_10))
                    log(f"  Unique in last 10: {unique_last_10}", level="ERROR")
                    log(f"  Last 3 hashes: {last_10[-3:]}", level="ERROR")
                
                if timestamps and len(timestamps) >= 10:
                    current_time = time.time()
                    oldest_hash_time = timestamps[-10]
                    newest_hash_time = timestamps[-1]
                    time_span = newest_hash_time - oldest_hash_time
                    
                    # Critical: Show age of hashes to verify they're recent
                    oldest_age = current_time - oldest_hash_time
                    newest_age = current_time - newest_hash_time
                    
                    log(f"  Hash history time span: {time_span:.1f}s", level="ERROR")
                    log(f"  Oldest hash age: {oldest_age:.1f}s ago", level="ERROR")
                    log(f"  Newest hash age: {newest_age:.1f}s ago", level="ERROR")
                    
                    # Warn if hashes are stale
                    if oldest_age > 300:  # More than 5 minutes old
                        log(f"  ⚠️  WARNING: Hashes are STALE (>5 minutes old)", level="ERROR")
                        log(f"  ⚠️  This indicates capture thread may be stuck", level="ERROR")
                    elif newest_age > 10:  # More than 10 seconds old
                        log(f"  ⚠️  WARNING: Newest hash is not recent (>10s old)", level="ERROR")
                        log(f"  ⚠️  Capture may have stopped or slowed", level="ERROR")
                    else:
                        log(f"  ✅ Hashes are RECENT (capture thread active)", level="ERROR")
            
            # 3. Motion Detector State
            md_health = self.motion_detector.get_health()
            log("Motion Detector State:", level="ERROR")
            log(f"  Thread alive: {md_health['thread_alive']}", level="ERROR")
            log(f"  Paused: {md_health['paused']}", level="ERROR")
            log(f"  In cooldown: {md_health['in_cooldown']}", level="ERROR")
            log(f"  Check count: {md_health['check_count']}", level="ERROR")
            
            # 4. Hardware Camera Detection
            camera_detected = self._check_camera_detected()
            log("Hardware State:", level="ERROR")
            log(f"  Camera detected by OS: {camera_detected}", level="ERROR")
            
            # 5. System Resources
            import psutil
            memory = psutil.virtual_memory()
            log("System Resources:", level="ERROR")
            log(f"  Memory usage: {memory.percent}%", level="ERROR")
            log(f"  Available memory: {memory.available / (1024**2):.0f} MB", level="ERROR")
            
            # 6. Picamera2 State (if accessible)
            if hasattr(self.circular_buffer, 'picam2') and self.circular_buffer.picam2:
                try:
                    log("Picamera2 State:", level="ERROR")
                    log(f"  Camera object exists: True", level="ERROR")
                    # Try to get camera properties
                    props = self.circular_buffer.picam2.camera_properties
                    if props:
                        model = props.get('Model', 'unknown')
                        log(f"  Camera model: {model}", level="ERROR")
                except Exception as e:
                    log(f"  Error accessing Picamera2: {e}", level="ERROR")
            
            log("="*60, level="ERROR")
            log("DIAGNOSTIC CAPTURE COMPLETE", level="ERROR")
            log("="*60, level="ERROR")
            log("", level="ERROR")
            log("RECOMMENDED ACTIONS:", level="ERROR")
            log("1. Check if camera hardware is responsive", level="ERROR")
            log("2. Try manually restarting the camera service", level="ERROR")
            log("3. Check for hardware issues (cable, power, heat)", level="ERROR")
            log("4. Review logs for exceptions before freeze", level="ERROR")
            log("="*60, level="ERROR")
            
        except Exception as e:
            log(f"Error capturing diagnostics: {e}", level="ERROR")
            import traceback
            traceback.print_exc()

    
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