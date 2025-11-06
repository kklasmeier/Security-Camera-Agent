#!/usr/bin/env python3
"""
Security Camera Diagnostic Script
==================================
Helps identify why the camera system is not working properly.

Run this script to check:
1. Camera hardware access
2. Configuration issues
3. Central server connectivity
4. File system permissions
5. Process conflicts
"""

import subprocess
import sys
from pathlib import Path
import time

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'
    BOLD = '\033[1m'

def print_header(text):
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*60}")
    print(f"{text}")
    print(f"{'='*60}{Colors.END}\n")

def print_success(message):
    print(f"  {Colors.GREEN}✓ {message}{Colors.END}")

def print_failure(message):
    print(f"  {Colors.RED}✗ {message}{Colors.END}")

def print_warning(message):
    print(f"  {Colors.YELLOW}⚠ {message}{Colors.END}")

def print_info(message):
    print(f"  {message}")

def run_command(cmd, description, show_output=False):
    """Run a shell command and return success status."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            print_success(description)
            if show_output and result.stdout:
                for line in result.stdout.strip().split('\n'):
                    print(f"    {line}")
            return True, result.stdout
        else:
            print_failure(f"{description} (exit code: {result.returncode})")
            if result.stderr:
                print_info(f"Error: {result.stderr.strip()}")
            return False, result.stderr
    except subprocess.TimeoutExpired:
        print_failure(f"{description} (timeout)")
        return False, "Timeout"
    except Exception as e:
        print_failure(f"{description} ({e})")
        return False, str(e)

def check_camera_hardware():
    """Check if camera hardware is accessible."""
    print_header("1. Camera Hardware Check")
    
    # Check if camera is detected
    print_info("Checking camera detection...")
    success, output = run_command(
        "rpicam-hello --list-cameras",
        "Camera device detected",
        show_output=False
    )
    
    if success:
        if "Available cameras" in output and "imx708" in output:
            print_info("Camera model: IMX708 (detected)")
        else:
            print_warning("Camera detected but model unclear")
    
    # Test basic camera access
    print_info("\nTesting camera capture...")
    success, _ = run_command(
        "rpicam-jpeg -o /tmp/diagnostic_test.jpg --timeout 1 --width 640 --height 480",
        "Camera can capture images"
    )
    
    if success:
        Path("/tmp/diagnostic_test.jpg").unlink(missing_ok=True)
    
    return success

def check_camera_processes():
    """Check for conflicting camera processes."""
    print_header("2. Process Conflict Check")
    
    print_info("Checking for processes using camera...")
    
    # Check for Python processes
    success, output = run_command(
        "ps aux | grep -E '(python|rpicam)' | grep -v grep",
        "Checking active camera processes",
        show_output=True
    )
    
    # Check for libcamera locks
    if Path("/tmp/").exists():
        lock_files = list(Path("/tmp/").glob("*libcamera*"))
        if lock_files:
            print_warning(f"Found {len(lock_files)} libcamera lock files:")
            for lock in lock_files:
                print_info(f"  {lock}")
            print_info("These may indicate a stale process")
        else:
            print_success("No stale libcamera locks found")

def check_configuration():
    """Check configuration file."""
    print_header("3. Configuration Check")
    
    config_path = Path.home() / "Security-Camera-Agent" / "config.py"
    
    if not config_path.exists():
        print_failure("config.py not found")
        return False
    
    print_success(f"Config file found: {config_path}")
    
    # Try to import config
    try:
        sys.path.insert(0, str(config_path.parent))
        import config as cfg
        
        print_info("\nKey configuration values:")
        print_info(f"  CAMERA_ID: {cfg.config.CAMERA_ID}")
        print_info(f"  CAMERA_NAME: {cfg.config.CAMERA_NAME}")
        print_info(f"  CENTRAL_SERVER_HOST: {cfg.config.CENTRAL_SERVER_HOST}")
        print_info(f"  CENTRAL_SERVER_PORT: {cfg.config.CENTRAL_SERVER_PORT}")
        print_info(f"  VIDEO_WIDTH: {cfg.config.VIDEO_WIDTH}")
        print_info(f"  VIDEO_HEIGHT: {cfg.config.VIDEO_HEIGHT}")
        print_info(f"  VIDEO_FRAMERATE: {cfg.config.VIDEO_FRAMERATE}")
        
        print_success("Configuration loaded successfully")
        return True
        
    except Exception as e:
        print_failure(f"Failed to load configuration: {e}")
        return False

def check_central_server():
    """Check connectivity to central server."""
    print_header("4. Central Server Connectivity")
    
    try:
        sys.path.insert(0, str(Path.home() / "Security-Camera-Agent"))
        from config import config as cfg
        
        host = cfg.CENTRAL_SERVER_HOST
        port = cfg.CENTRAL_SERVER_PORT
        
        print_info(f"Central server: {host}:{port}")
        
        # Ping test
        print_info(f"\nPinging {host}...")
        success, _ = run_command(
            f"ping -c 3 {host}",
            f"Network connectivity to {host}"
        )
        
        # HTTP test
        print_info(f"\nTesting HTTP connection...")
        success, _ = run_command(
            f"curl -s -o /dev/null -w '%{{http_code}}' http://{host}:{port}/api/v1/health --max-time 5",
            f"Central server API responding"
        )
        
        return success
        
    except Exception as e:
        print_failure(f"Cannot check central server: {e}")
        return False

def check_file_permissions():
    """Check file system permissions."""
    print_header("5. File System Permissions")
    
    try:
        sys.path.insert(0, str(Path.home() / "Security-Camera-Agent"))
        from config import config as cfg
        
        paths_to_check = [
            (Path(cfg.BASE_DIR), "Base directory"),
            (Path(cfg.TEMP_DIR), "Temp directory"),
            (Path(cfg.PENDING_DIR), "Pending directory"),
            (Path(cfg.NFS_MOUNT_POINT), "NFS mount point"),
        ]
        
        for path, description in paths_to_check:
            if path.exists():
                if path.is_dir():
                    # Test write permission
                    test_file = path / ".write_test"
                    try:
                        test_file.touch()
                        test_file.unlink()
                        print_success(f"{description}: exists and writable")
                    except Exception as e:
                        print_failure(f"{description}: not writable - {e}")
                else:
                    print_warning(f"{description}: exists but not a directory")
            else:
                print_failure(f"{description}: does not exist")
        
        return True
        
    except Exception as e:
        print_failure(f"Cannot check file permissions: {e}")
        return False

def check_dependencies():
    """Check Python dependencies."""
    print_header("6. Python Dependencies")
    
    required_modules = [
        "picamera2",
        "cv2",
        "numpy",
        "PIL",
        "flask",
        "requests"
    ]
    
    all_ok = True
    for module in required_modules:
        try:
            __import__(module)
            print_success(f"{module} installed")
        except ImportError:
            print_failure(f"{module} NOT installed")
            all_ok = False
    
    return all_ok

def check_system_resources():
    """Check system resources."""
    print_header("7. System Resources")
    
    # Memory
    print_info("Memory usage:")
    run_command(
        "free -h | head -2",
        "Memory check",
        show_output=True
    )
    
    # Disk space
    print_info("\nDisk space:")
    run_command(
        "df -h ~ | tail -1",
        "Disk space check",
        show_output=True
    )
    
    # CPU temperature
    print_info("\nCPU temperature:")
    run_command(
        "vcgencmd measure_temp",
        "CPU temperature check",
        show_output=True
    )

def main():
    """Run all diagnostic checks."""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*60}")
    print("Security Camera System Diagnostics")
    print(f"{'='*60}{Colors.END}\n")
    
    results = {
        "Camera Hardware": check_camera_hardware(),
        "Process Conflicts": check_camera_processes() is not None,  # Just checking, not pass/fail
        "Configuration": check_configuration(),
        "Central Server": check_central_server(),
        "File Permissions": check_file_permissions(),
        "Dependencies": check_dependencies(),
    }
    
    check_system_resources()
    
    # Summary
    print_header("Diagnostic Summary")
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for check_name, result in results.items():
        if result:
            print_success(f"{check_name}: OK")
        else:
            print_failure(f"{check_name}: ISSUE DETECTED")
    
    print(f"\n{Colors.BOLD}{passed}/{total} checks passed{Colors.END}\n")
    
    if passed == total:
        print(f"{Colors.GREEN}{Colors.BOLD}✓ All diagnostics passed!{Colors.END}")
        print("\nIf the system still doesn't work, try:")
        print("  1. Check the full logs in logs/runtime_*.log")
        print("  2. Look for motion detection activity (it may be working but in cooldown)")
        print("  3. Test motion detection by waving in front of camera")
    else:
        print(f"{Colors.RED}{Colors.BOLD}✗ Issues detected - see failures above{Colors.END}")
        print("\nRecommended actions:")
        
        if not results.get("Camera Hardware"):
            print("  - Ensure camera ribbon cable is properly connected")
            print("  - Check camera is enabled in raspi-config")
            print("  - Reboot the Raspberry Pi")
        
        if not results.get("Central Server"):
            print("  - Verify central server is running")
            print("  - Check network connection")
            print("  - Verify firewall settings")
        
        if not results.get("Dependencies"):
            print("  - Reinstall missing Python packages")
            print("  - Check virtual environment activation")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}Diagnostic interrupted by user{Colors.END}\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n{Colors.RED}Unexpected error: {e}{Colors.END}\n")
        import traceback
        traceback.print_exc()
        sys.exit(1)