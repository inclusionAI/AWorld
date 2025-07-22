#!/usr/bin/env python3
"""
Test script for GorillaFileSystem FastMCP Server

This script tests the GorillaFileSystem FastMCP server functionality.
Run this to verify the server works correctly.
"""

import asyncio
import sys
import os

# Add the current directory to the path to import gorilla_file_system
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gorilla_file_system import main, file_system


async def test_file_system():
    """Test the file system operations before starting the server."""
    print("🧪 Testing GorillaFileSystem operations...")
    
    # Test basic operations
    print("\n📁 Testing pwd:")
    result = file_system.pwd()
    print(f"Current directory: {result}")
    
    print("\n📋 Testing ls:")
    result = file_system.ls()
    print(f"Directory contents: {result}")
    
    print("\n📝 Testing touch:")
    result = file_system.touch("test_file.txt")
    print(f"Touch result: {result}")
    
    print("\n📝 Testing echo:")
    result = file_system.echo("Hello, FastMCP!", "test_file.txt")
    print(f"Echo result: {result}")
    
    print("\n📖 Testing cat:")
    result = file_system.cat("test_file.txt")
    print(f"Cat result: {result}")
    
    print("\n🔍 Testing find:")
    result = file_system.find(name="test")
    print(f"Find result: {result}")
    
    print("\n📊 Testing wc:")
    result = file_system.wc("test_file.txt", "w")
    print(f"Word count result: {result}")
    
    print("\n🗑️ Testing rm:")
    result = file_system.rm("test_file.txt")
    print(f"Remove result: {result}")
    
    print("\n✅ All basic tests passed! File system is working correctly.")
    print("\n🚀 Starting FastMCP server...")


async def main_test():
    """Main test function."""
    # Test file system operations first
    await test_file_system()
    
    # Start the FastMCP server
    await main()


if __name__ == "__main__":
    print("🔧 GorillaFileSystem FastMCP Server Test")
    print("=" * 50)
    asyncio.run(main_test()) 