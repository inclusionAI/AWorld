import pytest
import tempfile
import time
from pathlib import Path
from aworld.core.context.amni.prompt.neurons import aworld_file_neuron as aworld_file_neuron_module
from aworld.core.context.amni.prompt.neurons.aworld_file_neuron import AWORLDFileNeuron
from aworld_cli.memory.provider import InstructionContext
from aworld.core.context import ApplicationContext
from aworld.core.context.amni.state import ApplicationTaskContextState, TaskWorkingState, TaskInput, TaskOutput


def create_test_context(working_dir=None):
    """Helper function to create ApplicationContext for testing"""
    task_input = TaskInput(session_id="test_session", task_id="test_task", content="Test task")
    working_state = TaskWorkingState(messages=[], user_profiles=[], kv_store={})
    task_state = ApplicationTaskContextState(
        task_input=task_input,
        working_state=working_state,
        task_output=TaskOutput()
    )
    context = ApplicationContext(task_state=task_state)
    if working_dir:
        context.working_directory = working_dir
    return context


@pytest.fixture
def temp_aworld_file():
    """Create temporary AWORLD.md file"""
    with tempfile.TemporaryDirectory() as tmpdir:
        aworld_file = Path(tmpdir) / 'AWORLD.md'
        aworld_file.write_text("""
# Test Project Context

This is a test project.

## Guidelines
- Use Python 3.10+
- Follow PEP 8
""")
        yield tmpdir, aworld_file


@pytest.fixture
def temp_aworld_file_with_imports():
    """Create AWORLD.md with imports"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create main file
        aworld_file = Path(tmpdir) / 'AWORLD.md'
        aworld_file.write_text("""
# Main Context

@guidelines.md
@architecture.md
""")
        
        # Create imported files
        (Path(tmpdir) / 'guidelines.md').write_text("""
## Coding Guidelines
- Use type hints
- Write tests
""")
        
        (Path(tmpdir) / 'architecture.md').write_text("""
## Architecture
- Follow MVC pattern
- Use dependency injection
""")
        
        yield tmpdir, aworld_file


@pytest.mark.asyncio
async def test_aworld_file_neuron_basic(temp_aworld_file):
    """Test basic AWORLD.md loading"""
    tmpdir, aworld_file = temp_aworld_file
    
    # Create context with working directory
    context = create_test_context(working_dir=tmpdir)
    
    # Create neuron
    neuron = AWORLDFileNeuron()
    
    # Load content
    items = await neuron.format_items(context)
    
    assert len(items) == 1
    assert "Test Project Context" in items[0]
    assert "Python 3.10+" in items[0]


@pytest.mark.asyncio
async def test_aworld_file_neuron_with_imports(temp_aworld_file_with_imports):
    """Test AWORLD.md with @imports"""
    tmpdir, aworld_file = temp_aworld_file_with_imports
    
    context = create_test_context(working_dir=tmpdir)
    
    neuron = AWORLDFileNeuron()
    items = await neuron.format_items(context)
    
    assert len(items) == 1
    content = items[0]
    
    # Check main content
    assert "Main Context" in content
    
    # Check imported content
    assert "Coding Guidelines" in content
    assert "Use type hints" in content
    assert "Architecture" in content
    assert "MVC pattern" in content


@pytest.mark.asyncio
async def test_aworld_file_neuron_circular_import():
    """Test circular import detection"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create circular imports
        file_a = Path(tmpdir) / 'a.md'
        file_b = Path(tmpdir) / 'b.md'
        
        file_a.write_text("# File A\n@b.md")
        file_b.write_text("# File B\n@a.md")
        
        aworld_file = Path(tmpdir) / 'AWORLD.md'
        aworld_file.write_text("@a.md")
        
        context = create_test_context()
        context.working_directory = tmpdir
        
        neuron = AWORLDFileNeuron()
        items = await neuron.format_items(context)
        
        # Should handle gracefully
        assert len(items) == 1
        assert "Circular import" in items[0]


@pytest.mark.asyncio
async def test_aworld_file_neuron_caching():
    """Test content caching"""
    with tempfile.TemporaryDirectory() as tmpdir:
        aworld_file = Path(tmpdir) / 'AWORLD.md'
        aworld_file.write_text("# Version 1")
        
        context = create_test_context()
        context.working_directory = tmpdir
        
        neuron = AWORLDFileNeuron()
        
        # First load
        items1 = await neuron.format_items(context)
        assert "Version 1" in items1[0]
        
        # Second load (should use cache)
        items2 = await neuron.format_items(context)
        assert items1 == items2
        
        # Modify file
        time.sleep(0.1)  # Ensure mtime changes
        aworld_file.write_text("# Version 2")
        
        # Third load (should reload)
        items3 = await neuron.format_items(context)
        assert "Version 2" in items3[0]


@pytest.mark.asyncio
async def test_aworld_file_neuron_format():
    """Test formatted output"""
    with tempfile.TemporaryDirectory() as tmpdir:
        aworld_file = Path(tmpdir) / 'AWORLD.md'
        aworld_file.write_text("# Test")
        
        context = create_test_context()
        context.working_directory = tmpdir
        
        neuron = AWORLDFileNeuron()
        formatted = await neuron.format(context)
        
        assert "Project Context (from AWORLD.md)" in formatted
        assert "# Test" in formatted


@pytest.mark.asyncio
async def test_aworld_file_neuron_no_file():
    """Test behavior when no AWORLD.md file exists"""
    with tempfile.TemporaryDirectory() as tmpdir:
        context = create_test_context()
        context.working_directory = tmpdir
        
        neuron = AWORLDFileNeuron()
        items = await neuron.format_items(context)
        
        # Should return empty list
        assert len(items) == 0
        
        # Format should return empty string
        formatted = await neuron.format(context)
        assert formatted == ""


@pytest.mark.asyncio
async def test_aworld_file_neuron_desc():
    """Test neuron description"""
    context = create_test_context()
    neuron = AWORLDFileNeuron()
    
    desc = await neuron.desc(context)
    assert desc == "Project-specific context loaded from AWORLD.md file"


@pytest.mark.asyncio
async def test_aworld_file_neuron_import_not_found():
    """Test behavior when imported file doesn't exist"""
    with tempfile.TemporaryDirectory() as tmpdir:
        aworld_file = Path(tmpdir) / 'AWORLD.md'
        aworld_file.write_text("# Main\n@nonexistent.md")
        
        context = create_test_context()
        context.working_directory = tmpdir
        
        neuron = AWORLDFileNeuron()
        items = await neuron.format_items(context)
        
        assert len(items) == 1
        assert "Import not found" in items[0]


@pytest.mark.asyncio
async def test_aworld_file_neuron_nested_imports():
    """Test nested imports (import chain)"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create import chain: AWORLD.md -> level1.md -> level2.md
        aworld_file = Path(tmpdir) / 'AWORLD.md'
        aworld_file.write_text("# Root\n@level1.md")
        
        level1 = Path(tmpdir) / 'level1.md'
        level1.write_text("# Level 1\n@level2.md")
        
        level2 = Path(tmpdir) / 'level2.md'
        level2.write_text("# Level 2\nDeep content")
        
        context = create_test_context()
        context.working_directory = tmpdir
        
        neuron = AWORLDFileNeuron()
        items = await neuron.format_items(context)
        
        assert len(items) == 1
        content = items[0]
        
        # All levels should be present
        assert "Root" in content
        assert "Level 1" in content
        assert "Level 2" in content
        assert "Deep content" in content


@pytest.mark.asyncio
async def test_aworld_file_neuron_prefers_provider_instruction_context(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    class ProviderBackedMemory:
        def get_instruction_context(self, workspace_path):
            assert Path(workspace_path) == workspace
            return InstructionContext(
                texts=("global rule", "workspace rule"),
                warning=None,
                source_files=(),
            )

    memory_factory = type(
        "MemoryFactory",
        (),
        {"instance": staticmethod(lambda: ProviderBackedMemory())},
    )
    monkeypatch.setattr(
        aworld_file_neuron_module,
        "MemoryFactory",
        memory_factory,
        raising=False,
    )

    context = create_test_context(working_dir=str(workspace))
    neuron = AWORLDFileNeuron()

    items = await neuron.format_items(context)

    assert items == ["global rule", "workspace rule"]


@pytest.mark.asyncio
async def test_aworld_file_neuron_falls_back_to_legacy_file_when_provider_unavailable(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "AWORLD.md").write_text("legacy root file", encoding="utf-8")

    memory_factory = type(
        "MemoryFactory",
        (),
        {"instance": staticmethod(lambda: object())},
    )
    monkeypatch.setattr(
        aworld_file_neuron_module,
        "MemoryFactory",
        memory_factory,
        raising=False,
    )

    context = create_test_context(working_dir=str(workspace))
    neuron = AWORLDFileNeuron()

    items = await neuron.format_items(context)

    assert items == ["legacy root file"]
