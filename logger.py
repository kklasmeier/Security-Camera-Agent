"""
Security Camera System - Logging Module
========================================
Batched logging system that sends logs to central server via API.
Non-blocking with background writer thread.
Flushes logs every 5 seconds to reduce API calls.

Updated for Phase 1B: Multi-camera architecture
- Replaced database.py with api_client.py
- Logs sent to central server via REST API
- Local console logging as fallback
"""

import threading
import time
from datetime import datetime
from queue import Queue
from config import config
from api_client import APIClient


class APILogger:
    """
    Thread-safe batched logger that sends logs to central server via API.
    
    Logs are queued in memory and sent to central server in batches
    every LOG_BATCH_INTERVAL seconds (default: 5 seconds).
    
    This reduces API calls from hundreds per minute to ~12 per minute.
    
    Local console logging happens immediately for real-time monitoring.
    API logging is best-effort - if API fails, logs remain in console only.
    
    Usage:
        logger = APILogger()
        logger.log("System started")
        logger.log("Motion detected", level="INFO")
        logger.log("Camera error", level="ERROR")
        
        # When shutting down:
        logger.stop()
    """
    
    def __init__(self):
        """
        Initialize logger and start background writer thread.
        
        Creates API client for sending logs to central server.
        Starts background thread that flushes queued logs periodically.
        """
        self.api_client = APIClient()
        self.log_queue = Queue()
        self.running = True
        
        # Get batch interval from config (default 5 seconds)
        try:
            self.batch_interval = config.LOG_BATCH_INTERVAL
        except AttributeError:
            self.batch_interval = 5  # Fallback default
        
        # Start background writer thread
        self.writer_thread = threading.Thread(
            target=self._batch_writer,
            name="LogWriter",
            daemon=True
        )
        self.writer_thread.start()
        
        print(f"APILogger initialized - batching every {self.batch_interval} seconds")
    
    def log(self, message, level="INFO"):
        """
        Queue a log message for sending to central server.
        
        Non-blocking - returns immediately.
        Message is printed to console immediately for real-time monitoring.
        Actual API call happens in background every batch_interval seconds.
        
        Args:
            message (str): Log message
            level (str): Log level - "INFO", "WARNING", or "ERROR"
            
        Example:
            logger.log("Motion detected at front door")
            logger.log("Failed to save video", level="ERROR")
        """
        timestamp = datetime.now()
        
        # Validate level
        if level not in ["INFO", "WARNING", "ERROR", "DEBUG"]:
            level = "INFO"
        
        # Queue for batch sending to API
        self.log_queue.put((timestamp, level, message))
        
        # Also print to console immediately for real-time monitoring
        timestamp_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp_str}] [{level}] {message}")
    
    def _batch_writer(self):
        """
        Background thread that sends queued logs to central server.
        
        Runs in a loop, flushing logs every batch_interval seconds.
        This is a daemon thread and will automatically stop when main program exits.
        """
        while self.running:
            # Wait for batch interval
            time.sleep(self.batch_interval)
            
            # Flush any queued logs
            self._flush_logs()
    
    def _flush_logs(self):
        """
        Send all queued logs to central server via API.
        
        This is called automatically by the background writer thread,
        but can also be called manually to force immediate send.
        
        Logs are sent as a batch to reduce API calls.
        If API call fails, logs are lost (already printed to console).
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
                if not success:
                    # API failed, but logs are already in console
                    # No need to print error here as api_client already logged it
                    pass
            except Exception as e:
                # Unexpected error sending logs
                print(f"[ERROR] Failed to send log batch: {e}")
    
    def stop(self):
        """
        Stop the logger and flush any remaining logs.
        
        Should be called during graceful shutdown to ensure
        all queued logs are sent to central server.
        """
        print("APILogger stopping - flushing remaining logs...")
        self.running = False
        
        # Flush any remaining logs
        self._flush_logs()
        
        # Wait for writer thread to finish (with timeout)
        self.writer_thread.join(timeout=2.0)
        
        print("APILogger stopped")


# ============================================================================
# GLOBAL LOGGER INSTANCE
# ============================================================================

# Create a single global logger instance that all modules can use
_global_logger = None


def get_logger():
    """
    Get or create the global logger instance.
    
    This ensures all modules use the same logger instance,
    which is more efficient than creating multiple loggers.
    
    Returns:
        APILogger: Global logger instance
        
    Example:
        from logger import get_logger
        log = get_logger()
        log("System started")
    """
    global _global_logger
    
    if _global_logger is None:
        _global_logger = APILogger()
    
    return _global_logger


def log(message, level="INFO"):
    """
    Convenience function to log using the global logger.
    
    This is the recommended way to log from other modules.
    
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
    
    Example:
        from logger import stop_logger
        stop_logger()
    """
    global _global_logger
    
    if _global_logger is not None:
        _global_logger.stop()
        _global_logger = None


def log_memory_usage():
    """
    Log current memory usage for monitoring.
    Useful for debugging memory issues.
    """
    try:
        import psutil
        import os
        
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        mem_mb = mem_info.rss / (1024 * 1024)  # Convert to MB
        
        log(f"Memory usage: {mem_mb:.1f} MB", level="INFO")
        
    except ImportError:
        # psutil not available, use simpler method
        import resource
        mem_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        mem_mb = mem_kb / 1024  # Convert to MB
        log(f"Memory usage: ~{mem_mb:.1f} MB", level="INFO")
    except Exception as e:
        log(f"Could not log memory usage: {e}", level="WARNING")


# ============================================================================
# STANDALONE TESTING
# ============================================================================

if __name__ == "__main__":
    """
    Test logging functionality when run directly.
    """
    print("Testing APILogger...\n")
    
    # Test 1: Create logger
    print("--- Test 1: Creating logger ---")
    logger = APILogger()
    
    # Test 2: Log various messages
    print("\n--- Test 2: Logging messages ---")
    logger.log("System startup test")
    logger.log("Motion detected in zone 1", level="INFO")
    logger.log("Low disk space warning", level="WARNING")
    logger.log("Failed to save video file", level="ERROR")
    logger.log("Camera reconnected", level="INFO")
    
    print(f"\nWaiting {logger.batch_interval} seconds for batch send...")
    time.sleep(logger.batch_interval + 1)
    
    # Test 3: Multiple rapid logs
    print("\n--- Test 3: Rapid logging (10 messages) ---")
    for i in range(10):
        logger.log(f"Rapid test message {i+1}")
    
    print(f"Waiting {logger.batch_interval} seconds for batch send...")
    time.sleep(logger.batch_interval + 1)
    
    # Test 4: Test global logger functions
    print("\n--- Test 4: Testing global logger functions ---")
    log("Testing global log function")
    log("Testing with warning level", level="WARNING")
    log("Testing with error level", level="ERROR")
    
    # Test 5: Force flush
    print("\n--- Test 5: Force flush ---")
    logger.log("Final message before flush")
    logger._flush_logs()
    
    # Test 6: Graceful shutdown
    print("\n--- Test 6: Graceful shutdown ---")
    logger.log("Final message before shutdown")
    logger.stop()
    
    print("\n✓ All tests completed successfully!")
    print("\nCheck central server database to verify logs were received.")
    print(f"Expected logs from source: {config.CAMERA_ID}")