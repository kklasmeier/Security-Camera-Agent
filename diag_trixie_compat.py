#!/usr/bin/env python3
"""
Trixie Compatibility Check
===========================
Check for known compatibility issues with Debian Trixie
"""

import subprocess
import sys

print("=" * 60)
print("Checking Trixie Compatibility")
print("=" * 60)
print()

# Check libcamera version
print("1. Checking libcamera version...")
try:
    result = subprocess.run(
        ["dpkg", "-l", "libcamera0.3"],
        capture_output=True,
        text=True
    )
    if "libcamera" in result.stdout:
        print("✓ libcamera installed")
        for line in result.stdout.split('\n'):
            if 'libcamera' in line:
                print(f"  {line}")
    else:
        print("⚠️  libcamera version check inconclusive")
except Exception as e:
    print(f"✗ Error checking libcamera: {e}")

print()

# Check picamera2
print("2. Checking picamera2...")
try:
    import picamera2
    print(f"✓ picamera2 version: {picamera2.__version__}")
    
    # Try to instantiate (without starting camera)
    from picamera2 import Picamera2
    print("✓ picamera2 can be imported")
    
except ImportError as e:
    print(f"✗ picamera2 not installed: {e}")
    print()
    print("ACTION: Install picamera2:")
    print("  sudo apt install python3-picamera2")
except Exception as e:
    print(f"✗ picamera2 error: {e}")

print()

# Check for known Trixie issues
print("3. Checking for known Trixie issues...")

# Issue 1: Camera permissions
try:
    import os
    import grp
    
    # Check if user is in video group
    video_gid = grp.getgrnam('video').gr_gid
    user_groups = os.getgroups()
    
    if video_gid in user_groups:
        print("✓ User is in 'video' group")
    else:
        print("✗ User NOT in 'video' group")
        print()
        print("ACTION: Add user to video group:")
        print("  sudo usermod -a -G video $USER")
        print("  (then log out and back in)")
except Exception as e:
    print(f"⚠️  Could not check video group: {e}")

print()

# Issue 2: /dev/video* permissions
print("4. Checking /dev/video* devices...")
try:
    import glob
    video_devices = glob.glob('/dev/video*')
    
    if video_devices:
        print(f"✓ Found {len(video_devices)} video devices:")
        for dev in video_devices:
            print(f"  {dev}")
    else:
        print("⚠️  No /dev/video* devices found")
        print()
        print("ACTION: Check camera interface:")
        print("  sudo raspi-config")
        print("  → Interface Options → Camera → Enable")
except Exception as e:
    print(f"✗ Error checking video devices: {e}")

print()
print("=" * 60)
print("Compatibility Check Complete")
print("=" * 60)