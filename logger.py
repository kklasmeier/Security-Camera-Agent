"""
Security Camera System - Enhanced Logging Module
=================================================
Triple logging system:
1. Local file logs (immediate, survives network issues)
2. Console output (for systemd/run.sh)
3. Central server API (batched, for centralized monitoring)

Features:
- Thread-safe file writing
- Automatic log rotation (daily files)
- Automatic purge of logs older than LOG_RETENTION_DAYS
- Non-blocking API sends
- Configurable log levels per destination
- Memory-efficient batching

FIXED: Removed os.fsync() to prevent midnight deadlock
"""

import threading
import time
import os
from datetime import datetime, timedelta
from queue import Queue
from pathlib import Path
from config import config
from api_client import APIClient


class EnhancedLogger:
    """
    Thread-safe logger with file, console, and API destinations.
    
    Logs are:
    1. Written to daily log files immediately (thread-safe)
    2. Printed to console immediately (for real-time monitoring)
    3. Batched and sent to API every LOG_BATCH_INTERVAL seconds
    
    Usage:
        logger = EnhancedLogger()
        logger.log("System started")
        logger.log("Motion detected", level="INFO")
        logger.log("Camera error", level="ERROR")
        
        # When shutting down:
        logger.stop()
    """
    
    # Maximum logs per API batch to prevent HTTP 422 errors
    # Conservative limit to ensure reliable transmission
    MAX_BATCH_SIZE = 20
    
    # Maximum message length per log entry (prevent oversized payloads)
    MAX_MESSAGE_LENGTH = 1000
    
    # Adaptive flushing thresholds
    FAST_FLUSH_THRESHOLD = 10  # If queue has this many logs, flush more frequently
    FAST_FLUSH_INTERVAL = 0.5  # Flush every 0.5 seconds when queue is backing up
    
    def __init__(self, log_dir=None):
        """
        Initialize logger with file, console, and API destinations.
        
        Args:
            log_dir (str, optional): Directory for log files
                                     Defaults to config.BASE_PATH/logs
        """
        # Setup log directory
        if log_dir is None:
            self.log_dir = Path(config.BASE_PATH) / "logs"
        else:
            self.log_dir = Path(log_dir)
        
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # API client for central server logging
        self.api_client = APIClient()
        
        # Queue for API batching
        self.log_queue = Queue()
        
        # File writing lock (thread-safe)
        self.file_lock = threading.Lock()
        
        # Current log file
        self.current_log_file = None
        self.current_date = None
        self._open_log_file()
        self._purge_old_logs()
        
        # Control flags
        self.running = True
        
        # Get batch interval from config
        try:
            self.batch_interval = config.LOG_BATCH_INTERVAL
        except AttributeError:
            self.batch_interval = 5  # Fallback default
        
        # Start background API writer thread
        self.writer_thread = threading.Thread(
            target=self._batch_writer,
            name="LogWriter",
            daemon=True
        )
        self.writer_thread.start()
        
        # Log initialization
        retention_days = self._get_retention_days()
        init_msg = (f"Enhanced Logger initialized\n"
                   f"  Log directory: {self.log_dir}\n"
                   f"  Log retention: {retention_days} days\n"
                   f"  API batching: every {self.batch_interval}s\n"
                   f"  Camera: {config.CAMERA_ID}")
        print(init_msg)
        self._write_to_file("="*60, skip_timestamp=True)
        self._write_to_file(init_msg)
        self._write_to_file("="*60, skip_timestamp=True)
    
    def _get_retention_days(self):
        try:
            return int(config.LOG_RETENTION_DAYS)
        except AttributeError:
            return 14
    
    def _purge_old_logs(self):
        """Delete runtime_YYYYMMDD.log files older than LOG_RETENTION_DAYS."""
        retention_days = self._get_retention_days()
        if retention_days <= 0:
            return
        
        cutoff = datetime.now().date() - timedelta(days=retention_days)
        deleted = 0
        
        for path in self.log_dir.glob("runtime_*.log"):
            if self.current_date and path.name == f"runtime_{self.current_date}.log":
                continue
            try:
                file_date = datetime.strptime(
                    path.stem.replace("runtime_", ""), "%Y%m%d"
                ).date()
            except ValueError:
                continue
            if file_date < cutoff:
                try:
                    path.unlink()
                    deleted += 1
                    print(f"[LOG PURGE] Deleted old log: {path.name}")
                except OSError as e:
                    print(f"[WARNING] Could not delete {path.name}: {e}")
        
        if deleted:
            print(f"[LOG PURGE] Removed {deleted} log file(s) older than {retention_days} days")
    
    def _open_log_file(self):
        """
        Open log file for current date.
        
        Creates daily log files: runtime_YYYYMMDD.log
        Automatically rotates to new file when date changes.
        
        CRITICAL FIX: This method MUST NOT be called while holding file_lock
        to prevent deadlock. Only call from _write_to_file which holds the lock.
        """
        current_date = datetime.now().strftime("%Y%m%d")
        
        # Check if we need to rotate to new file
        if current_date != self.current_date:
            print(f"[LOG ROTATION] Starting rotation to {current_date}")
            
            # Store reference to old file
            old_file = self.current_log_file
            
            # Build new log file path
            log_filename = f"runtime_{current_date}.log"
            log_path = self.log_dir / log_filename
            
            try:
                # Open new file FIRST (before closing old)
                # Use line buffering (1) for automatic flushing per line
                print(f"[LOG ROTATION] Opening new file: {log_path}")
                new_file = open(log_path, 'a', buffering=1)
                
                # Write rotation marker to new file immediately
                timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                new_file.write(f"{'='*60}\n")
                new_file.write(f"[{timestamp_str}] Log file rotated to: {log_filename}\n")
                new_file.write(f"{'='*60}\n")
                new_file.flush()  # Flush only, no fsync
                
                # Atomic switch to new file
                self.current_log_file = new_file
                self.current_date = current_date
                
                print(f"[LOG ROTATION] Switched to new file")
                
                # Close old file safely (after switch is complete)
                if old_file is not None:
                    try:
                        old_file.flush()
                        old_file.close()
                        print(f"[LOG ROTATION] Old file closed")
                    except Exception as e:
                        print(f"[WARNING] Error closing old log file: {e}")
                
                print(f"[LOG ROTATION] Rotation complete!")
                self._purge_old_logs()
                
            except Exception as e:
                error_msg = f"Failed to rotate log file: {e}"
                print(f"[ERROR] {error_msg}")
                import traceback
                traceback.print_exc()
                
                # Revert to old file if still available
                if old_file is not None and not old_file.closed:
                    self.current_log_file = old_file
                    print(f"[LOG ROTATION] Reverted to old file")
    
    def _write_to_file(self, message, skip_timestamp=False):
        """
        Write message to log file (thread-safe).
        
        CRITICAL: Removed os.fsync() to prevent deadlock.
        Uses line buffering instead for automatic flushing.
        
        Args:
            message (str): Message to write
            skip_timestamp (bool): If True, don't add timestamp (for separators)
        """
        with self.file_lock:
            try:
                # Check if we need to rotate log file
                # This is safe because we hold file_lock
                self._open_log_file()
                
                if self.current_log_file and not self.current_log_file.closed:
                    if skip_timestamp:
                        self.current_log_file.write(f"{message}\n")
                    else:
                        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self.current_log_file.write(f"[{timestamp_str}] {message}\n")
                    
                    # Only flush, don't fsync (fsync can block indefinitely)
                    # Line buffering ensures writes happen immediately anyway
                    self.current_log_file.flush()
                    
            except Exception as e:
                # Fallback to console only if file write fails
                print(f"[ERROR] Failed to write to log file: {e}")
    
    def log(self, message, level="INFO"):
        """
        Log message to all destinations: file, console, and API.
        
        Non-blocking - returns immediately.
        File writing happens immediately (thread-safe).
        API call happens in background every batch_interval seconds.
        
        Args:
            message (str): Log message
            level (str): Log level - "INFO", "WARNING", "ERROR", "DEBUG"
            
        Example:
            logger.log("Motion detected at front door")
            logger.log("Failed to save video", level="ERROR")
        """
        timestamp = datetime.now()
        
        # Validate level
        if level not in ["INFO", "WARNING", "ERROR", "DEBUG"]:
            level = "INFO"
        
        # Format message with level
        formatted_message = f"[{level}] {message}"
        
        # 1. Write to file immediately (thread-safe)
        self._write_to_file(formatted_message)
        
        # 2. Print to console immediately
        timestamp_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp_str}] {formatted_message}")
        
        # 3. Queue for API batch send (skip DEBUG)
        if level in ["INFO", "WARNING", "ERROR"]:
            self.log_queue.put((timestamp, level, message))
    
    def _batch_writer(self):
        """
        Background thread that sends queued logs to central server.
        
        Uses adaptive timing:
        - Normal mode: Flushes every batch_interval seconds (typically 5s)
        - Fast mode: When queue has >= FAST_FLUSH_THRESHOLD logs, flushes every 0.5s
        
        This prevents queue buildup during heavy logging periods while maintaining
        efficiency during normal operation.
        
        This is a daemon thread and will automatically stop when main program exits.
        """
        while self.running:
            # Check queue size for adaptive timing
            queue_size = self.log_queue.qsize()
            
            if queue_size >= self.FAST_FLUSH_THRESHOLD:
                # Fast mode: Queue is backing up, flush more frequently
                wait_time = self.FAST_FLUSH_INTERVAL
            else:
                # Normal mode: Standard interval
                wait_time = self.batch_interval
            
            # Wait for the determined interval
            time.sleep(wait_time)
            
            # Flush any queued logs to API
            self._flush_logs()
    
    def _flush_logs(self):
        """
        Send queued logs to central server via API.
        
        Called automatically by background writer thread.
        Can also be called manually to force immediate send.
        
        Logs are sent in batches up to MAX_BATCH_SIZE to prevent HTTP 422 errors.
        If more logs remain in queue, they'll be sent in the next batch cycle.
        If API call fails, logs remain in local file.
        """
        if self.log_queue.empty():
            return
        
        # Track initial queue size for debugging
        initial_queue_size = self.log_queue.qsize()
        
        # Collect logs up to MAX_BATCH_SIZE
        log_batch = []
        batch_count = 0
        items_pulled = 0  # Track how many we've pulled from queue
        
        while not self.log_queue.empty() and items_pulled < self.MAX_BATCH_SIZE:
            try:
                timestamp, level, message = self.log_queue.get_nowait()
                items_pulled += 1  # Count items pulled, even if skipped
                
                # Truncate message if too long to prevent HTTP 422 errors
                if len(message) > self.MAX_MESSAGE_LENGTH:
                    message = message[:self.MAX_MESSAGE_LENGTH - 3] + "..."
                
                # Skip empty messages (would fail server validation)
                if not message or not message.strip():
                    continue  # Skip but don't add to batch
                
                # Sanitize message - remove any problematic characters
                message = message.strip()
                
                # Format for API
                log_entry = {
                    "source": self.api_client.camera_id,
                    "timestamp": timestamp.isoformat(),
                    "level": level,
                    "message": message
                }
                log_batch.append(log_entry)
                batch_count += 1
            except:
                break
        
        # Send batch to central server via API
        if log_batch:
            remaining = self.log_queue.qsize()
            try:
                success = self.api_client.send_logs(log_batch)
                if success:
                    # Log detailed info about the flush
                    if remaining > 0 or initial_queue_size > self.FAST_FLUSH_THRESHOLD:
                        self._write_to_file(
                            f"[DEBUG] Sent {len(log_batch)} log entries to API "
                            f"(queue: {initial_queue_size} → {remaining})"
                        )
                else:
                    # Log when send fails
                    self._write_to_file(
                        f"[WARNING] Failed to send {len(log_batch)} log entries "
                        f"(queue: {initial_queue_size}, remaining: {remaining})"
                    )
                # If failed, logs are still in local file (which is the important part)
            except Exception as e:
                # Log API errors to file only (not console to avoid spam)
                self._write_to_file(
                    f"[ERROR] Failed to send log batch to API: {e} "
                    f"(attempted {len(log_batch)} logs, queue had {initial_queue_size})"
                )
    
    def stop(self):
        """
        Stop the logger and flush any remaining logs.
        
        Ensures all queued logs are sent to API and file is closed properly.
        """
        print("Enhanced Logger stopping...")
        self.log("Enhanced Logger shutting down gracefully")
        
        self.running = False
        
        # Flush any remaining logs to API
        self._flush_logs()
        
        # Wait for writer thread to finish
        if self.writer_thread and self.writer_thread.is_alive():
            self.writer_thread.join(timeout=2.0)
        
        # Close log file
        with self.file_lock:
            if self.current_log_file:
                try:
                    self._write_to_file("="*60, skip_timestamp=True)
                    self._write_to_file("Logger stopped - end of session")
                    self._write_to_file("="*60, skip_timestamp=True)
                    self.current_log_file.flush()
                    self.current_log_file.close()
                    self.current_log_file = None
                except:
                    pass
        
        print("Enhanced Logger stopped")


# ============================================================================
# GLOBAL LOGGER INSTANCE
# ============================================================================

_global_logger = None


def get_logger():
    """
    Get or create the global logger instance.
    
    Returns:
        EnhancedLogger: Global logger instance
    """
    global _global_logger
    
    if _global_logger is None:
        _global_logger = EnhancedLogger()
    
    return _global_logger


def log(message, level="INFO"):
    """
    Convenience function to log using the global logger.
    
    Args:
        message (str): Log message
        level (str): Log level - "INFO", "WARNING", "ERROR", "DEBUG"
        
    Example:
        from logger import log
        
        log("Motion detected")
        log("Camera error", level="ERROR")
    """
    logger = get_logger()
    logger.log(message, level)


def stop_logger():
    """
    Stop the global logger and flush remaining logs.
    
    Should be called during system shutdown.
    """
    global _global_logger
    
    if _global_logger is not None:
        _global_logger.stop()
        _global_logger = None


def log_memory_usage():
    """
    Log current memory usage for monitoring.
    """
    try:
        import psutil
        import os
        
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        mem_mb = mem_info.rss / (1024 * 1024)
        
        log(f"[MEMDEBUG] RSS={mem_mb:.1f} MB", level="INFO")
        
    except ImportError:
        import resource
        mem_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        mem_mb = mem_kb / 1024
        log(f"[MEMDEBUG] RSS=~{mem_mb:.1f} MB", level="INFO")
    except Exception as e:
        log(f"Could not log memory usage: {e}", level="WARNING")


# ============================================================================
# STANDALONE TESTING
# ============================================================================

if __name__ == "__main__":
    """
    Test enhanced logging functionality.
    """
    print("Testing Enhanced Logger...\n")
    
    # Test 1: Create logger
    print("--- Test 1: Creating logger ---")
    logger = EnhancedLogger()
    
    # Test 2: Log various levels
    print("\n--- Test 2: Logging different levels ---")
    logger.log("System startup test")
    logger.log("Motion detected in zone 1", level="INFO")
    logger.log("Low disk space warning", level="WARNING")
    logger.log("Failed to save video file", level="ERROR")
    logger.log("Debug message (not sent to API)", level="DEBUG")
    
    print(f"\nWaiting {logger.batch_interval} seconds for API batch send...")
    time.sleep(logger.batch_interval + 1)
    
    # Test 3: Rapid logging
    print("\n--- Test 3: Rapid logging ---")
    for i in range(5):
        logger.log(f"Rapid test message {i+1}")
    
    # Test 4: Global functions
    print("\n--- Test 4: Global logger functions ---")
    log("Testing global log function")
    log("Testing with warning level", level="WARNING")
    
    # Test 5: Graceful shutdown
    print("\n--- Test 5: Graceful shutdown ---")
    logger.log("Final message before shutdown")
    logger.stop()
    
    print("\n✓ All tests completed!")
    print(f"\nCheck log file at: {logger.log_dir}/runtime_{datetime.now().strftime('%Y%m%d')}.log")
    print("Check central server for API logs")