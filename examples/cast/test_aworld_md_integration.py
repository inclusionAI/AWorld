"""
Integration test for AWORLD.md functionality
Tests the complete flow of AWORLD.md loading and integration with the memory system
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from aworld.core.context.amni import ApplicationContext, TaskInput, AmniConfigFactory
from aworld.core.context.amni.config import AmniConfigLevel
from aworld.core.context.amni.prompt.neurons.aworld_file_neuron import AWORLDFileNeuron


async def test_basic_loading():
    """Test 1: Basic AWORLD.md loading"""
    print("\n" + "="*60)
    print("TEST 1: Basic AWORLD.md Loading")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create AWORLD.md
        aworld_file = Path(tmpdir) / '.aworld' / 'AWORLD.md'
        aworld_file.parent.mkdir(parents=True, exist_ok=True)
        aworld_file.write_text("""
# Test Project Context

## Project Overview
This is a test project for AWORLD.md functionality.

## Guidelines
- Use Python 3.10+
- Follow PEP 8
- Write comprehensive tests
""")
        
        # Create context
        task_input = TaskInput(
            user_id="test_user",
            session_id="test_session",
            task_id="test_task",
            task_content="test",
            origin_user_input="test"
        )
        
        context_config = AmniConfigFactory.create(AmniConfigLevel.PILOT)
        context = await ApplicationContext.from_input(task_input, context_config=context_config)
        context.working_directory = tmpdir
        
        # Create neuron and load content
        neuron = AWORLDFileNeuron()
        items = await neuron.format_items(context)
        
        # Verify
        assert len(items) == 1, f"Expected 1 item, got {len(items)}"
        assert "Test Project Context" in items[0], "Content not loaded correctly"
        assert "Python 3.10+" in items[0], "Guidelines not loaded"
        
        print("✅ Basic loading works")
        print(f"   Loaded {len(items[0])} characters")
        print(f"   Content preview: {items[0][:100]}...")


async def test_import_functionality():
    """Test 2: @import syntax"""
    print("\n" + "="*60)
    print("TEST 2: @import Functionality")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create main file
        aworld_file = Path(tmpdir) / '.aworld' / 'AWORLD.md'
        aworld_file.parent.mkdir(parents=True, exist_ok=True)
        aworld_file.write_text("""
# Main Context

@guidelines.md
@architecture.md
""")
        
        # Create imported files
        (Path(tmpdir) / '.aworld' / 'guidelines.md').write_text("""
## Coding Guidelines
- Use type hints
- Write tests
""")
        
        (Path(tmpdir) / '.aworld' / 'architecture.md').write_text("""
## Architecture
- Follow MVC pattern
- Use dependency injection
""")
        
        # Create context
        task_input = TaskInput(
            user_id="test_user",
            session_id="test_session",
            task_id="test_task",
            task_content="test",
            origin_user_input="test"
        )
        
        context_config = AmniConfigFactory.create(AmniConfigLevel.PILOT)
        context = await ApplicationContext.from_input(task_input, context_config=context_config)
        context.working_directory = tmpdir
        
        # Load content
        neuron = AWORLDFileNeuron()
        items = await neuron.format_items(context)
        
        # Verify
        content = items[0]
        assert "Main Context" in content, "Main content not found"
        assert "Coding Guidelines" in content, "Imported guidelines not found"
        assert "Use type hints" in content, "Guidelines details not found"
        assert "Architecture" in content, "Imported architecture not found"
        assert "MVC pattern" in content, "Architecture details not found"
        
        print("✅ Import functionality works")
        print("   ✓ Main content loaded")
        print("   ✓ guidelines.md imported")
        print("   ✓ architecture.md imported")


async def test_circular_import_detection():
    """Test 3: Circular import detection"""
    print("\n" + "="*60)
    print("TEST 3: Circular Import Detection")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create circular imports
        file_a = Path(tmpdir) / '.aworld' / 'a.md'
        file_b = Path(tmpdir) / '.aworld' / 'b.md'
        file_a.parent.mkdir(parents=True, exist_ok=True)
        
        file_a.write_text("# File A\n@b.md")
        file_b.write_text("# File B\n@a.md")
        
        aworld_file = Path(tmpdir) / '.aworld' / 'AWORLD.md'
        aworld_file.write_text("@a.md")
        
        # Create context
        task_input = TaskInput(
            user_id="test_user",
            session_id="test_session",
            task_id="test_task",
            task_content="test",
            origin_user_input="test"
        )
        
        context_config = AmniConfigFactory.create(AmniConfigLevel.PILOT)
        context = await ApplicationContext.from_input(task_input, context_config=context_config)
        context.working_directory = tmpdir
        
        # Load content
        neuron = AWORLDFileNeuron()
        items = await neuron.format_items(context)
        
        # Verify
        assert len(items) == 1, "Should handle circular imports gracefully"
        assert "Circular import" in items[0], "Circular import not detected"
        
        print("✅ Circular import detection works")
        print("   ✓ Detected circular reference")
        print("   ✓ Handled gracefully")


async def test_caching():
    """Test 4: Content caching"""
    print("\n" + "="*60)
    print("TEST 4: Content Caching")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        aworld_file = Path(tmpdir) / '.aworld' / 'AWORLD.md'
        aworld_file.parent.mkdir(parents=True, exist_ok=True)
        aworld_file.write_text("# Version 1")
        
        # Create context
        task_input = TaskInput(
            user_id="test_user",
            session_id="test_session",
            task_id="test_task",
            task_content="test",
            origin_user_input="test"
        )
        
        context_config = AmniConfigFactory.create(AmniConfigLevel.PILOT)
        context = await ApplicationContext.from_input(task_input, context_config=context_config)
        context.working_directory = tmpdir
        
        neuron = AWORLDFileNeuron()
        
        # First load
        items1 = await neuron.format_items(context)
        assert "Version 1" in items1[0], "First load failed"
        
        # Second load (should use cache)
        items2 = await neuron.format_items(context)
        assert items1 == items2, "Cache not working"
        
        # Modify file
        import time
        time.sleep(0.1)  # Ensure mtime changes
        aworld_file.write_text("# Version 2")
        
        # Third load (should reload)
        items3 = await neuron.format_items(context)
        assert "Version 2" in items3[0], "Reload failed"
        assert items3 != items1, "Should have reloaded"
        
        print("✅ Caching works correctly")
        print("   ✓ First load successful")
        print("   ✓ Cache used on second load")
        print("   ✓ Reloaded after file modification")


async def test_formatted_output():
    """Test 5: Formatted output"""
    print("\n" + "="*60)
    print("TEST 5: Formatted Output")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        aworld_file = Path(tmpdir) / '.aworld' / 'AWORLD.md'
        aworld_file.parent.mkdir(parents=True, exist_ok=True)
        aworld_file.write_text("# Test Content")
        
        # Create context
        task_input = TaskInput(
            user_id="test_user",
            session_id="test_session",
            task_id="test_task",
            task_content="test",
            origin_user_input="test"
        )
        
        context_config = AmniConfigFactory.create(AmniConfigLevel.PILOT)
        context = await ApplicationContext.from_input(task_input, context_config=context_config)
        context.working_directory = tmpdir
        
        neuron = AWORLDFileNeuron()
        formatted = await neuron.format(context)
        
        # Verify
        assert "Project Context (from AWORLD.md)" in formatted, "Header not found"
        assert "# Test Content" in formatted, "Content not found"
        assert "---" in formatted, "Footer not found"
        
        print("✅ Formatted output works")
        print("   ✓ Header present")
        print("   ✓ Content formatted")
        print("   ✓ Footer present")


async def test_no_file():
    """Test 6: Behavior when no file exists"""
    print("\n" + "="*60)
    print("TEST 6: No File Handling")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create context without AWORLD.md
        task_input = TaskInput(
            user_id="test_user",
            session_id="test_session",
            task_id="test_task",
            task_content="test",
            origin_user_input="test"
        )
        
        context_config = AmniConfigFactory.create(AmniConfigLevel.PILOT)
        context = await ApplicationContext.from_input(task_input, context_config=context_config)
        context.working_directory = tmpdir
        
        neuron = AWORLDFileNeuron()
        items = await neuron.format_items(context)
        
        # Verify
        assert len(items) == 0, "Should return empty list when no file"
        
        formatted = await neuron.format(context)
        assert formatted == "", "Should return empty string when no file"
        
        print("✅ No file handling works")
        print("   ✓ Returns empty list")
        print("   ✓ Returns empty string for format")


async def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("AWORLD.md Integration Test Suite")
    print("="*60)
    
    tests = [
        test_basic_loading,
        test_import_functionality,
        test_circular_import_detection,
        test_caching,
        test_formatted_output,
        test_no_file,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            await test()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"\n❌ Test failed: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "="*60)
    print("Test Summary")
    print("="*60)
    print(f"Total: {len(tests)}")
    print(f"✅ Passed: {passed}")
    print(f"❌ Failed: {failed}")
    
    if failed == 0:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print(f"\n⚠️  {failed} test(s) failed")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
