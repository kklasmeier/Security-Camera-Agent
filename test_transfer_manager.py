#!/usr/bin/env python3
"""
TransferManager Test Suite
===========================
Comprehensive testing for Session 1B-7 Transfer Manager

This script tests:
1. Filename parsing
2. NFS mount detection
3. File transfer simulation
4. Error handling

Usage:
    python test_transfer_manager.py
"""

import os
import sys
from pathlib import Path
import tempfile
import shutil

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from config import config
from api_client import APIClient
from transfer_manager import TransferManager


class Colors:
    """ANSI color codes for pretty output"""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'
    BOLD = '\033[1m'


def print_header(text):
    """Print formatted section header"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*60}")
    print(f"{text}")
    print(f"{'='*60}{Colors.END}\n")


def print_test(name):
    """Print test name"""
    print(f"{Colors.BOLD}Test: {name}{Colors.END}")


def print_success(message):
    """Print success message"""
    print(f"  {Colors.GREEN}✓ {message}{Colors.END}")


def print_failure(message):
    """Print failure message"""
    print(f"  {Colors.RED}✗ {message}{Colors.END}")


def print_warning(message):
    """Print warning message"""
    print(f"  {Colors.YELLOW}⚠ {message}{Colors.END}")


def test_filename_parsing():
    """Test filename parsing logic"""
    print_header("Test 1: Filename Parsing")
    
    # Create transfer manager
    api_client = APIClient()
    tm = TransferManager(api_client)
    
    test_cases = [
        # (filename, expected_event_id, expected_type, expected_dest, should_succeed)
        ("42_20251030_143022_a.jpg", 42, "image_a", "pictures", True),
        ("42_20251030_143022_b.jpg", 42, "image_b", "pictures", True),
        ("42_20251030_143022_thumb.jpg", 42, "thumbnail", "thumbs", True),
        ("42_20251030_143022_video.h264", 42, "video", "videos", True),
        ("100_20251101_120000_a.jpg", 100, "image_a", "pictures", True),
        ("invalid_filename.jpg", None, None, None, False),
        ("42_a.jpg", None, None, None, False),
        ("not_enough_parts.jpg", None, None, None, False),
    ]
    
    passed = 0
    failed = 0
    
    for filename, exp_id, exp_type, exp_dest, should_succeed in test_cases:
        print_test(f"Parse: {filename}")
        result = tm._parse_filename(filename)
        
        if should_succeed:
            if result and result['event_id'] == exp_id and result['file_type'] == exp_type and result['dest_subdir'] == exp_dest:
                print_success(f"Parsed correctly: event_id={exp_id}, type={exp_type}, dest={exp_dest}")
                passed += 1
            else:
                print_failure(f"Parse failed or incorrect result: {result}")
                failed += 1
        else:
            if result is None:
                print_success("Correctly rejected invalid filename")
                passed += 1
            else:
                print_failure(f"Should have rejected but parsed as: {result}")
                failed += 1
    
    print(f"\n{Colors.BOLD}Filename Parsing: {passed} passed, {failed} failed{Colors.END}")
    return failed == 0


def test_nfs_mount():
    """Test NFS mount detection"""
    print_header("Test 2: NFS Mount Detection")
    
    api_client = APIClient()
    tm = TransferManager(api_client)
    
    print_test("Check NFS mount")
    
    if tm._check_nfs_mounted():
        print_success(f"NFS mounted at: {tm.nfs_base}")
        print_success(f"NFS structure: flat (no camera subdirectory)")
        
        # Check subdirectories
        for subdir in ['pictures', 'thumbs', 'videos']:
            path = tm.camera_nfs_dir / subdir
            if path.exists():
                print_success(f"  Subdirectory exists: {subdir}/")
            else:
                print_warning(f"  Subdirectory missing: {subdir}/")
        
        return True
    else:
        print_failure(f"NFS not mounted or subdirectories missing")
        print_warning(f"Expected mount: {tm.nfs_base}")
        print_warning(f"Required subdirectories: pictures/, thumbs/, videos/")
        print_warning("Verify with:")
        print_warning(f"  ls -la {tm.nfs_base}/")
        return False


def test_transfer_simulation():
    """Test file transfer with temporary files"""
    print_header("Test 3: Transfer Simulation")
    
    api_client = APIClient()
    tm = TransferManager(api_client)
    
    # Check if NFS is available
    if not tm._check_nfs_mounted():
        print_warning("NFS not mounted - skipping transfer simulation")
        return False
    
    # Create temporary test file
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        
        # Create test file
        test_file = tmpdir_path / "999_20251101_120000_a.jpg"
        test_file.write_text("TEST DATA - NOT A REAL IMAGE")
        
        print_test(f"Simulate transfer: {test_file.name}")
        
        # Parse filename
        file_info = tm._parse_filename(test_file.name)
        if not file_info:
            print_failure("Failed to parse test filename")
            return False
        
        print_success(f"Parsed: event_id={file_info['event_id']}, type={file_info['file_type']}")
        
        # Simulate transfer (but use a test subdirectory to avoid conflicts)
        dest_dir = tm.camera_nfs_dir / "pictures"
        dest_file = dest_dir / f"TEST_{test_file.name}"
        temp_file = dest_file.with_suffix(dest_file.suffix + '.tmp')
        
        try:
            # Copy to .tmp
            shutil.copy2(test_file, temp_file)
            print_success(f"Created temp file: {temp_file.name}")
            
            # Atomic rename
            temp_file.rename(dest_file)
            print_success(f"Atomic rename successful: {dest_file.name}")
            
            # Verify file exists
            if dest_file.exists():
                print_success("File exists on NFS")
                
                # Cleanup
                dest_file.unlink()
                print_success("Cleanup successful")
                
                return True
            else:
                print_failure("File not found after rename")
                return False
                
        except Exception as e:
            print_failure(f"Transfer simulation failed: {e}")
            
            # Cleanup on error
            if temp_file.exists():
                temp_file.unlink()
            if dest_file.exists():
                dest_file.unlink()
            
            return False


def test_configuration():
    """Test configuration settings"""
    print_header("Test 4: Configuration")
    
    print_test("Check transfer settings")
    
    try:
        print_success(f"TRANSFER_CHECK_INTERVAL: {config.TRANSFER_CHECK_INTERVAL}s")
        print_success(f"TRANSFER_TIMEOUT: {config.TRANSFER_TIMEOUT}s")
        
        # Verify removed settings
        if hasattr(config, 'TRANSFER_MAX_RETRIES'):
            print_failure("TRANSFER_MAX_RETRIES should be removed!")
            return False
        else:
            print_success("TRANSFER_MAX_RETRIES correctly removed")
        
        if hasattr(config, 'TRANSFER_RETRY_DELAY'):
            print_failure("TRANSFER_RETRY_DELAY should be removed!")
            return False
        else:
            print_success("TRANSFER_RETRY_DELAY correctly removed")
        
        return True
        
    except Exception as e:
        print_failure(f"Configuration error: {e}")
        return False


def test_pending_directory():
    """Test pending directory setup"""
    print_header("Test 5: Pending Directory")
    
    api_client = APIClient()
    tm = TransferManager(api_client)
    
    print_test(f"Check pending directory: {tm.pending_dir}")
    
    if tm.pending_dir.exists():
        print_success(f"Pending directory exists")
        
        # Check if writable
        test_file = tm.pending_dir / ".write_test"
        try:
            test_file.touch()
            test_file.unlink()
            print_success("Pending directory is writable")
            return True
        except Exception as e:
            print_failure(f"Pending directory not writable: {e}")
            return False
    else:
        print_failure("Pending directory does not exist")
        return False


def main():
    """Run all tests"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*60}")
    print("TransferManager Test Suite")
    print("Session 1B-7 Verification")
    print(f"{'='*60}{Colors.END}\n")
    
    results = {
        "Filename Parsing": test_filename_parsing(),
        "NFS Mount Detection": test_nfs_mount(),
        "Configuration": test_configuration(),
        "Pending Directory": test_pending_directory(),
        "Transfer Simulation": test_transfer_simulation(),
    }
    
    # Summary
    print_header("Test Summary")
    
    passed = sum(1 for v in results.values() if v)
    failed = sum(1 for v in results.values() if not v)
    
    for test_name, result in results.items():
        if result:
            print_success(f"{test_name}: PASSED")
        else:
            print_failure(f"{test_name}: FAILED")
    
    print(f"\n{Colors.BOLD}Overall: {passed}/{len(results)} tests passed{Colors.END}\n")
    
    if failed == 0:
        print(f"{Colors.GREEN}{Colors.BOLD}✓ All tests passed! TransferManager is ready.{Colors.END}\n")
        return 0
    else:
        print(f"{Colors.RED}{Colors.BOLD}✗ {failed} test(s) failed. Review errors above.{Colors.END}\n")
        return 1


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}Test interrupted by user{Colors.END}\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n{Colors.RED}{Colors.BOLD}Unexpected error: {e}{Colors.END}\n")
        import traceback
        traceback.print_exc()
        sys.exit(1)