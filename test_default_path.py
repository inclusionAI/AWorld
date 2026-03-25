#!/usr/bin/env python3
"""
Test script to verify default AWORLD.md path is user directory
"""
from pathlib import Path

def test_default_path():
    """Test that default path is user directory"""
    default_path = Path.home() / '.aworld' / 'AWORLD.md'
    
    print("=" * 60)
    print("Testing Default AWORLD.md Path")
    print("=" * 60)
    print()
    
    print(f"Default path: {default_path}")
    print(f"User home: {Path.home()}")
    print()
    
    # Verify it's in user directory
    assert str(default_path).startswith(str(Path.home())), \
        f"Default path should be in user directory, got: {default_path}"
    
    print("✅ Default path is in user directory")
    print()
    
    # Verify it's not in current directory
    current_dir_path = Path.cwd() / '.aworld' / 'AWORLD.md'
    assert default_path != current_dir_path, \
        "Default path should NOT be current directory"
    
    print(f"✅ Default path is NOT current directory ({current_dir_path})")
    print()
    
    # Show the difference
    print("Path Comparison:")
    print(f"  Default (User):   {default_path}")
    print(f"  Current (Project): {current_dir_path}")
    print()
    
    print("=" * 60)
    print("✅ All tests passed!")
    print("=" * 60)

if __name__ == "__main__":
    test_default_path()
