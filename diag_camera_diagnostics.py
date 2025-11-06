#!/usr/bin/env python3
"""
Camera System Diagnostics
=========================
Checks the health of the camera system to identify issues with motion detection.
"""

import threading
import time
import psutil
import os
from datetime import datetime


def check_threads():
    """Check all running threads."""
    print("\n" + "="*60)
    print("THREAD STATUS")
    print("="*60)
    
    threads = threading.enumerate()
    print(f"Total active threads: {len(threads)}\n")
    
    expected_threads = [
        "MainThread",
        "CameraCapture",
        "MotionDetector", 
        "EventProcessor",
        "MJPEGServer",
        "TransferManager",
        "CameraControlAPI"
    ]
    
    found_threads = {}
    for thread in threads:
        found_threads[thread.name] = {
            'alive': thread.is_alive(),
            'daemon': thread.daemon,
            'ident': thread.ident
        }
        status = "âœ"" if thread.is_alive() else "âœ—"
        daemon_str = "(daemon)" if thread.daemon else ""
        print(f"{status} {thread.name:20s} {daemon_str}")
    
    print("\nMissing expected threads:")
    for expected in expected_threads:
        if expected not in found_threads:
            print(f"  âœ— {expected}")
    
    return found_threads


def check_memory():
    """Check memory usage."""
    print("\n" + "="*60)
    print("MEMORY STATUS")
    print("="*60)
    
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    
    print(f"RSS (Resident Set Size): {mem_info.rss / 1024 / 1024:.1f} MB")
    print(f"VMS (Virtual Memory Size): {mem_info.vms / 1024 / 1024:.1f} MB")
    
    # System memory
    vm = psutil.virtual_memory()
    print(f"\nSystem Memory:")
    print(f"  Total: {vm.total / 1024 / 1024:.1f} MB")
    print(f"  Available: {vm.available / 1024 / 1024:.1f} MB")
    print(f"  Used: {vm.percent}%")
    
    if vm.percent > 90:
        print("  âš ï¸ WARNING: High memory usage!")
    
    return mem_info.rss / 1024 / 1024


def check_camera_state(api_url="http://localhost:5000"):
    """Check camera state via API."""
    print("\n" + "="*60)
    print("CAMERA API STATUS")
    print("="*60)
    
    try:
        import requests
        
        # Check health endpoint
        response = requests.get(f"{api_url}/api/health", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print("âœ" API responding")
            print(f"\nCamera: {data.get('name', 'Unknown')}")
            print(f"Status: {data.get('status', 'Unknown')}")
            print(f"Motion State: {data.get('motion_state', 'Unknown')}")
            print(f"Streaming: {data.get('streaming', False)}")
            print(f"Capture Interval: {data.get('capture_interval', 'Unknown')}s")
            print(f"Uptime: {data.get('uptime_seconds', 0)}s")
            
            return data
        else:
            print(f"âœ— API returned status {response.status_code}")
            return None
            
    except ImportError:
        print("âœ— requests module not available")
        print("Install with: pip install requests")
        return None
    except Exception as e:
        print(f"âœ— Error connecting to API: {e}")
        return None


def check_files(pending_dir="/home/pi/security_camera/pending"):
    """Check pending files."""
    print("\n" + "="*60)
    print("PENDING FILES")
    print("="*60)
    
    try:
        from pathlib import Path
        pending = Path(pending_dir)
        
        if not pending.exists():
            print(f"âœ— Pending directory doesn't exist: {pending_dir}")
            return
        
        files = list(pending.glob("*"))
        sentinel_files = list(pending.glob("*.READY"))
        media_files = [f for f in files if f.suffix in ['.jpg', '.h264']]
        
        print(f"Total files: {len(files)}")
        print(f"Media files: {len(media_files)}")
        print(f"Sentinel files: {len(sentinel_files)}")
        
        if len(media_files) > 100:
            print(f"\nâš ï¸ WARNING: {len(media_files)} media files pending transfer!")
            print("  This could indicate transfer issues.")
        
        # Show newest files
        if files:
            newest = sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)[:5]
            print("\nNewest files:")
            for f in newest:
                age = time.time() - f.stat().st_mtime
                print(f"  {f.name} (age: {age:.0f}s)")
                
    except Exception as e:
        print(f"Error checking files: {e}")


def continuous_monitor(interval=10, duration=60):
    """Continuously monitor for issues."""
    print("\n" + "="*60)
    print("CONTINUOUS MONITORING")
    print("="*60)
    print(f"Monitoring for {duration} seconds (interval: {interval}s)")
    print("Press Ctrl+C to stop early\n")
    
    start_time = time.time()
    check_count = 0
    
    try:
        while time.time() - start_time < duration:
            check_count += 1
            timestamp = datetime.now().strftime("%H:%M:%S")
            
            # Quick thread check
            threads = threading.enumerate()
            thread_names = [t.name for t in threads if t.is_alive()]
            
            # Check for critical threads
            has_motion = "MotionDetector" in thread_names
            has_processor = "EventProcessor" in thread_names
            has_capture = "CameraCapture" in thread_names
            
            status = "âœ"" if (has_motion and has_processor and has_capture) else "âœ—"
            
            print(f"[{timestamp}] Check #{check_count}: {status} "
                  f"Motion={has_motion} Processor={has_processor} Capture={has_capture}")
            
            if not has_motion:
                print("  âš ï¸ ALERT: MotionDetector thread missing!")
            if not has_processor:
                print("  âš ï¸ ALERT: EventProcessor thread missing!")
            if not has_capture:
                print("  âš ï¸ ALERT: CameraCapture thread missing!")
            
            time.sleep(interval)
            
    except KeyboardInterrupt:
        print("\n\nMonitoring stopped by user")
    
    print(f"\nCompleted {check_count} checks")


def main():
    """Run all diagnostics."""
    print("\n" + "="*80)
    print("SECURITY CAMERA SYSTEM DIAGNOSTICS")
    print("="*80)
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Run checks
    threads = check_threads()
    mem_mb = check_memory()
    camera_data = check_camera_state()
    check_files()
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    
    issues = []
    
    if "MotionDetector" not in threads:
        issues.append("MotionDetector thread not running")
    if "EventProcessor" not in threads:
        issues.append("EventProcessor thread not running")
    if "CameraCapture" not in threads:
        issues.append("CameraCapture thread not running")
    
    if mem_mb > 400:  # Pi Zero 2W has 512MB
        issues.append(f"High memory usage: {mem_mb:.1f} MB")
    
    if camera_data:
        if camera_data.get('streaming'):
            issues.append("Camera is in streaming mode (motion paused)")
    
    if issues:
        print("âœ— Issues found:")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print("âœ" No obvious issues detected")
    
    # Offer continuous monitoring
    print("\n" + "="*60)
    response = input("\nRun continuous monitoring? (y/n): ").strip().lower()
    if response == 'y':
        continuous_monitor()


if __name__ == "__main__":
    main()