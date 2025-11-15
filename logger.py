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
- Non-blocking API sends
- Configurable log levels per destination
- Memory-efficient batching

Updated for Phase 1B: Multi-camera architecture
"""

import threading
import time
import os
from datetime import datetime
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
        init_msg = (f"Enhanced Logger initialized\n"
                   f"  Log directory: {self.log_dir}\n"
                   f"  API batching: every {self.batch_interval}s\n"
                   f"  Camera: {config.CAMERA_ID}")
        print(init_msg)
        self._write_to_file("="*60, skip_timestamp=True)
        self._write_to_file(init_msg)
        self._write_to_file("="*60, skip_timestamp=True)
    
    def _open_log_file(self):
        """
        Open log file for current date.
        
        Creates daily log files: runtime_YYYYMMDD.log
        Automatically rotates to new file when date changes.
        """
        current_date = datetime.now().strftime("%Y%m%d")
        
        # Check if we need to rotate to new file
        if current_date != self.current_date:
            # Close previous file if open
            if self.current_log_file is not None:
                try:
                    self.current_log_file.close()
                except:
                    pass
            
            # Open new file for today
            log_filename = f"runtime_{current_date}.log"
            log_path = self.log_dir / log_filename
            
            # Open in append mode (survives restarts)
            self.current_log_file = open(log_path, 'a', buffering=1)  # Line buffered
            self.current_date = current_date
            
            # Log file rotation
            if self.current_log_file:
                rotation_msg = f"Log file opened: {log_filename}"
                self._write_to_file("="*60, skip_timestamp=True)
                self._write_to_file(rotation_msg)
                self._write_to_file("="*60, skip_timestamp=True)
    
    def _write_to_file(self, message, skip_timestamp=False):
        """
        Write message to log file (thread-safe).
        
        Args:
            message (str): Message to write
            skip_timestamp (bool): If True, don't add timestamp (for separators)
        """
        with self.file_lock:
            try:
                # Check if we need to rotate log file
                self._open_log_file()
                
                if self.current_log_file:
                    if skip_timestamp:
                        self.current_log_file.write(f"{message}\n")
                    else:
                        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self.current_log_file.write(f"[{timestamp_str}] {message}\n")
                    
                    # Flush to disk immediately (important for debugging)
                    self.current_log_file.flush()
                    os.fsync(self.current_log_file.fileno())
                    
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
        
        Runs in a loop, flushing logs every batch_interval seconds.
        This is a daemon thread and will automatically stop when main program exits.
        """
        while self.running:
            # Wait for batch interval
            time.sleep(self.batch_interval)
            
            # Flush any queued logs to API
            self._flush_logs()
    
    def _flush_logs(self):
        """
        Send all queued logs to central server via API.
        
        Called automatically by background writer thread.
        Can also be called manually to force immediate send.
        
        Logs are sent as a batch to reduce API calls.
        If API call fails, logs remain in local file.
        """
        if self.log_queue.empty():
            return
        
        # Collect all queued logs
        log_batch = []
        while not self.log_queue.empty():
            try:
                timestamp, level, message = self.log_queue.get_nowait()
                
                # Format for API
                log_entry = {
                    "source": self.api_client.camera_id,
                    "timestamp": timestamp.isoformat(),
                    "level": level,
                    "message": message
                }
                log_batch.append(log_entry)
            except:
                break
        
        # Send batch to central server via API
        if log_batch:
            try:
                success = self.api_client.send_logs(log_batch)
                if success:
                    # Optional: log successful API send to file only (not console to avoid spam)
                    # self._write_to_file(f"[DEBUG] Sent {len(log_batch)} log entries to central server")
                    pass
                # If failed, logs are still in local file (which is the important part)
            except Exception as e:
                # Log API errors to file only (not console to avoid spam)
                self._write_to_file(f"[ERROR] Failed to send log batch to API: {e}")
    
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