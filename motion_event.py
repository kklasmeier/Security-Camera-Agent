"""
Security Camera System - Motion Event Coordination
===================================================
Coordinates motion detection signals between Thread 2 and Thread 3.

Thread 2 (motion_detector) calls set() when motion detected (non-blocking)
Thread 3 (event_processor) calls wait_and_get() to wait for motion (blocking)

No queuing needed - timing guarantees sequential processing.
"""

import threading
from datetime import datetime
from logger import log


class MotionEvent:
    """
    Thread-safe event coordination for motion detection.
    
    Allows Thread 2 to signal Thread 3 when motion is detected,
    passing event_id and timestamp as payload.
    
    Usage:
        # Shared between threads:
        motion_event = MotionEvent()
        
        # Thread 2 (motion detector):
        motion_event.set(event_id=42, timestamp=datetime.now())
        
        # Thread 3 (event processor):
        data = motion_event.wait_and_get()  # Blocks until motion
        event_id = data['event_id']
        timestamp = data['timestamp']
    """
    
    def __init__(self):
        """
        Initialize motion event coordinator.
        """
        self._event = threading.Event()
        self._data = {}
        self._lock = threading.Lock()
        
        log("MotionEvent coordinator initialized")
    
    def set(self, event_id, timestamp):
        """
        Signal that motion was detected (called by Thread 2).
        
        Non-blocking - returns immediately.
        Thread 2 continues to cooldown without waiting.
        
        Args:
            event_id (int): Database event ID for tracking
            timestamp (datetime): When motion occurred
            
        Example:
            motion_event.set(event_id=42, timestamp=datetime.now())
        """
        with self._lock:
            self._data = {
                'event_id': event_id,
                'timestamp': timestamp
            }
        
        # Signal Thread 3 that motion occurred
        self._event.set()
        
        log(f"Motion event set: event_id={event_id}")
    
    def wait_and_get(self):
        """
        Wait for motion event signal (called by Thread 3).
        
        Blocks efficiently until motion is detected.
        Returns event data when motion occurs.
        
        Returns:
            dict: {
                'event_id': int,      # Database event ID
                'timestamp': datetime # When motion occurred
            }
            
        Example:
            data = motion_event.wait_and_get()  # Blocks here
            event_id = data['event_id']
            timestamp = data['timestamp']
        """
        # Block here until Thread 2 calls set()
        # Uses efficient OS-level waiting (no CPU usage)
        self._event.wait()
        
        # Get the event data
        with self._lock:
            data = self._data.copy()
        
        # Reset event for next motion detection
        self._event.clear()
        
        log(f"Motion event received: event_id={data['event_id']}")
        
        return data
    
    def is_set(self):
        """
        Check if motion event is currently signaled.
        
        Returns:
            bool: True if motion event is pending, False otherwise
            
        Note:
            This is typically not needed in normal operation,
            but useful for debugging or testing.
        """
        return self._event.is_set()


# ============================================================================
# STANDALONE TESTING
# ============================================================================

if __name__ == "__main__":
    """
    Test MotionEvent coordination between threads.
    """
    import time
    
    print("Testing MotionEvent coordination...\n")
    
    # Create motion event coordinator
    motion_event = MotionEvent()
    
    # Flag to track if Thread 3 received the event
    received = {'flag': False, 'data': None}
    
    # Thread 3 simulation (event processor)
    def thread3_simulator():
        print("Thread 3: Waiting for motion event...")
        data = motion_event.wait_and_get()
        print(f"Thread 3: Received event! event_id={data['event_id']}, "
              f"timestamp={data['timestamp']}")
        received['flag'] = True
        received['data'] = data
    
    # Start Thread 3
    thread3 = threading.Thread(target=thread3_simulator, daemon=True)
    thread3.start()
    
    # Give Thread 3 a moment to start waiting
    time.sleep(0.5)
    
    # Thread 2 simulation (motion detector)
    print("\nThread 2: Motion detected! Signaling Thread 3...")
    test_timestamp = datetime.now()
    motion_event.set(event_id=123, timestamp=test_timestamp)
    print("Thread 2: Signal sent, continuing with cooldown...")
    
    # Wait for Thread 3 to process
    time.sleep(1.0)
    
    # Verify
    print("\n" + "="*60)
    if received['flag']:
        print("✓ Test PASSED!")
        print(f"  Event ID: {received['data']['event_id']}")
        print(f"  Timestamp: {received['data']['timestamp']}")
        print("  Thread coordination working correctly!")
    else:
        print("✗ Test FAILED - Thread 3 did not receive event")
    print("="*60)