#!/usr/bin/env python3
"""
Test script for Config class refactor - Session 1B-1
Validates all success criteria from specification
"""

import sys
import os

# Add parent directory to Python path so we can import config
# This allows the test to run from ~/Security-Camera-Agent/testing/
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import config, Config, ensure_directories, validate_config, print_config

print("="*70)
print("CONFIG REFACTOR - TEST SUITE")
print("="*70)

# ============================================================================
# Test 1: Config Object Creation
# ============================================================================
print("\n[Test 1] Config Object Creation")
try:
    from config import config
    print(f"  CAMERA_ID: {config.CAMERA_ID}")
    print("  ✓ Config object created successfully")
except Exception as e:
    print(f"  ✗ FAILED: {e}")
    sys.exit(1)

# ============================================================================
# Test 2: All Existing Settings Available
# ============================================================================
print("\n[Test 2] All Existing Settings Available")
try:
    assert config.MOTION_THRESHOLD == 60
    assert config.VIDEO_RESOLUTION == (1280, 720)
    assert config.CIRCULAR_BUFFER_MAX_CHUNKS == 1000
    assert config.VIDEO_FRAMERATE == 15
    assert config.MOTION_COOLDOWN_SECONDS == 65
    print("  ✓ All existing settings accessible")
except AssertionError as e:
    print(f"  ✗ FAILED: Assertion error - {e}")
    sys.exit(1)

# ============================================================================
# Test 3: New Settings Available
# ============================================================================
print("\n[Test 3] New Settings Available")
try:
    assert config.CAMERA_ID is not None
    assert config.CAMERA_NAME is not None
    assert config.CAMERA_LOCATION is not None
    assert config.CENTRAL_SERVER_HOST is not None
    assert config.CENTRAL_SERVER_PORT is not None
    assert config.PENDING_DIR is not None
    assert config.NFS_MOUNT_PATH is not None
    assert config.TRANSFER_CHECK_INTERVAL == 0.25
    assert config.TRANSFER_MAX_RETRIES == 10
    assert config.LOG_DESTINATION == "api"
    print("  ✓ All new settings accessible")
except AssertionError as e:
    print(f"  ✗ FAILED: Assertion error - {e}")
    sys.exit(1)

# ============================================================================
# Test 4: Directory Creation
# ============================================================================
print("\n[Test 4] Directory Creation")
try:
    ensure_directories()
    print("  ✓ Directories created without errors")
except Exception as e:
    print(f"  ✗ FAILED: {e}")
    sys.exit(1)

# ============================================================================
# Test 5: Validation
# ============================================================================
print("\n[Test 5] Config Validation")
try:
    validate_config()
    print("  ✓ Validation completed (warnings are OK)")
except ValueError as e:
    print(f"  ✗ FAILED: {e}")
    sys.exit(1)

# ============================================================================
# Test 6: Config Display
# ============================================================================
print("\n[Test 6] Config Display")
try:
    print("\n" + "-"*70)
    print_config()
    print("-"*70)
    print("  ✓ Config display completed")
except Exception as e:
    print(f"  ✗ FAILED: {e}")
    sys.exit(1)

# ============================================================================
# Test 7: Multiple Instances (for testing)
# ============================================================================
print("\n[Test 7] Multiple Config Instances")
try:
    config1 = Config()
    config1.CAMERA_ID = "camera_1"
    
    config2 = Config()
    config2.CAMERA_ID = "camera_2"
    
    assert config1.CAMERA_ID == "camera_1"
    assert config2.CAMERA_ID == "camera_2"
    print("  ✓ Multiple config instances work independently")
except AssertionError as e:
    print(f"  ✗ FAILED: Assertion error - {e}")
    sys.exit(1)

# ============================================================================
# Test 8: Backward Compatibility (import patterns)
# ============================================================================
print("\n[Test 8] Import Patterns")
try:
    # Pattern 1: Import config object directly
    from config import config as cfg1
    assert cfg1.MOTION_THRESHOLD == 60
    
    # Pattern 2: Import Config class for instantiation
    from config import Config
    cfg2 = Config()
    assert cfg2.MOTION_THRESHOLD == 60
    
    print("  ✓ All import patterns work correctly")
except Exception as e:
    print(f"  ✗ FAILED: {e}")
    sys.exit(1)

# ============================================================================
# Test 9: Verify Critical Path Values
# ============================================================================
print("\n[Test 9] Verify Critical Path Values")
try:
    assert config.BASE_PATH == "/home/pi/Security-Camera-Agent"
    assert config.NFS_MOUNT_PATH == "/home/pi/Security-Camera-Agent/security_footage"
    assert config.TMP_PATH == "/home/pi/Security-Camera-Agent/tmp"
    assert config.PENDING_DIR == "/home/pi/Security-Camera-Agent/tmp/pending"
    assert config.VIDEO_PATH == "/home/pi/Security-Camera-Agent/security_footage/videos"
    assert config.PICTURES_PATH == "/home/pi/Security-Camera-Agent/security_footage/pictures"
    assert config.THUMBS_PATH == "/home/pi/Security-Camera-Agent/security_footage/thumbs"
    print("  ✓ All critical paths are correct")
except AssertionError as e:
    print(f"  ✗ FAILED: Path assertion error")
    sys.exit(1)

# ============================================================================
# Test 10: Verify Camera Identity
# ============================================================================
print("\n[Test 10] Verify Camera Identity")
try:
    assert config.CAMERA_ID == "camera_1"
    assert config.CAMERA_NAME == "Front Walkway"
    assert config.CAMERA_LOCATION == "Study"
    print("  ✓ Camera identity configured correctly")
except AssertionError as e:
    print(f"  ✗ FAILED: Camera identity assertion error")
    sys.exit(1)

# ============================================================================
# Test 11: Verify Central Server Settings
# ============================================================================
print("\n[Test 11] Verify Central Server Settings")
try:
    assert config.CENTRAL_SERVER_HOST == "192.168.1.26"
    assert config.CENTRAL_SERVER_PORT == 8000
    assert config.CENTRAL_SERVER_API_BASE == "http://192.168.1.26:8000/api/v1"
    print("  ✓ Central server settings configured correctly")
except AssertionError as e:
    print(f"  ✗ FAILED: Central server assertion error")
    sys.exit(1)

# ============================================================================
# Test 12: Verify Video Format Change
# ============================================================================
print("\n[Test 12] Verify Video Format Change")
try:
    assert config.VIDEO_OUTPUT_FORMAT == "h264"
    print("  ✓ Video format changed to h264 (MP4 conversion on server)")
except AssertionError as e:
    print(f"  ✗ FAILED: Video format should be 'h264', not '{config.VIDEO_OUTPUT_FORMAT}'")
    sys.exit(1)

# ============================================================================
# SUMMARY
# ============================================================================
print("\n" + "="*70)
print("ALL TESTS PASSED ✓")
print("="*70)
print("\nConfig refactor is complete and functional!")
print("\nNext Steps:")
print("  1. Deploy to /home/pi/Security-Camera-Agent/config.py")
print("  2. Update other modules to use: from config import config")
print("  3. Proceed with Session 1B-2 (API Client Module)")
print("="*70)