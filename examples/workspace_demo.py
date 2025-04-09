#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Workspace Management Demo
Demonstrates basic workspace management functionality
"""

import os
import sys
from pathlib import Path

# Add project root directory to Python path
current_dir = Path(__file__).parent
root_dir = current_dir.parent
sys.path.insert(0, str(root_dir))

from aworld.output.artifact import ArtifactType
from aworld.output.workspace import WorkSpace


def main():
    """Workspace management basic demonstration"""
    print("===== Workspace Management Demo =====")
    
    # Create working directory
    os.makedirs("data/workspace_demo", exist_ok=True)
    
    # Create workspace
    workspace = WorkSpace(name="Example Workspace", storage_path="data/workspace_demo")
    print(f"Created workspace: {workspace.name} (ID: {workspace.workspace_id})")

    # Create several different types of artifacts
    print("\nCreating artifacts...")
    
    # Text artifact
    text_artifact = workspace.create_artifact(
        artifact_type=ArtifactType.TEXT,
        content="This is a simple text artifact.",
        metadata={"description": "Demo text artifact"}
    )
    print(f"Created text artifact (ID: {text_artifact.artifact_id})")
    
    # Code artifact
    code_artifact = workspace.create_artifact(
        artifact_type=ArtifactType.CODE,
        content="def hello_world():\n    print('Hello, World!')",
        metadata={"language": "python"}
    )
    print(f"Created code artifact (ID: {code_artifact.artifact_id})")
    
    # JSON artifact
    json_artifact = workspace.create_artifact(
        artifact_type=ArtifactType.JSON,
        content={"name": "Example JSON", "value": 42},
        metadata={"description": "Demo JSON artifact"}
    )
    print(f"Created JSON artifact (ID: {json_artifact.artifact_id})")
    
    # List all artifacts
    print("\nArtifacts in workspace:")
    for artifact in workspace.list_artifacts():
        print(f"- {artifact.artifact_id}: {artifact.artifact_type.value}, created at {artifact.created_at}")
    
    # Update artifact content
    print("\nUpdating code artifact...")
    updated_code = "def hello_world():\n    print('Hello, World!')\n\ndef goodbye():\n    print('Goodbye!')"
    workspace.update_artifact(
        artifact_id=code_artifact.artifact_id,
        content=updated_code,
        description="Added goodbye function"
    )
    
    # View artifact version history
    code_artifact = workspace.get_artifact(code_artifact.artifact_id)
    print(f"\nVersion history for code artifact {code_artifact.artifact_id}:")
    for i, version in enumerate(code_artifact.version_history):
        print(f"- Version {i}: {version['description']}, time: {version['timestamp']}")
    
    # Save workspace
    print("\nSaving workspace...")
    workspace_version_id = workspace.save()
    print(f"Workspace saved (Version ID: {workspace_version_id})")
    
    # Delete an artifact
    print("\nDeleting JSON artifact...")
    workspace.delete_artifact(json_artifact.artifact_id)
    print(f"Remaining artifacts: {len(workspace.list_artifacts())}")
    
    # Reload workspace
    print("\nReloading workspace...")
    loaded_workspace = WorkSpace.load(workspace.workspace_id, "data/workspace_demo")
    print(f"Loaded workspace: {loaded_workspace.name}")
    print(f"Number of artifacts: {len(loaded_workspace.artifacts)}")
    
    # Display loaded artifacts
    print("\nLoaded artifacts list:")
    for artifact in loaded_workspace.list_artifacts():
        print(f"- {artifact.artifact_id}: {artifact.artifact_type.value}")
        # Display brief information about artifact content
        if artifact.artifact_type == ArtifactType.TEXT:
            print(f"  Content: {artifact.content}")
        elif artifact.artifact_type == ArtifactType.CODE:
            code_lines = artifact.content.split('\n')
            print(f"  Code lines: {len(code_lines)}")
            if len(code_lines) > 1:
                print(f"  First line: {code_lines[0]}")
    
    print("\nDemo completed!")


if __name__ == "__main__":
    main() 