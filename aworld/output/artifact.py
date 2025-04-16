import uuid
from datetime import datetime
from enum import Enum, auto
from typing import Dict, Any, Optional, ClassVar
from pydantic import Field, field_validator, model_validator

from aworld.output.base import Output


class ArtifactType(Enum):
    """Defines supported artifact types"""
    TEXT = "TEXT"
    CODE = "CODE"
    MARKDOWN = "MARKDOWN"
    HTML = "HTML"
    SVG = "SVG"
    JSON = "JSON"
    CSV = "CSV"
    TABLE = "TABLE"
    CHART = "CHART"
    DIAGRAM = "DIAGRAM"
    MCP_CALL = "MCP_CALL"
    TOOL_CALL = "TOOL_CALL"
    LLM_OUTPUT = "LLM_OUTPUT"
    WEB_PAGES = "WEB_PAGES"
    DIR = "DIR"
    CUSTOM = "CUSTOM"



class ArtifactStatus(Enum):
    """Artifact status"""
    DRAFT = auto()      # Draft status
    COMPLETE = auto()   # Completed status
    EDITED = auto()     # Edited status
    ARCHIVED = auto()   # Archived status

class Artifact(Output):
    """
    Represents a specific content generation result (artifact)
    
    Artifacts are the basic units of Artifacts technology, representing a structured content unit
    Can be code, markdown, charts, and various other formats
    """

    artifact_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique identifier for the artifact")
    artifact_type: ArtifactType = Field(..., description="Type of the artifact")
    content: Any = Field(..., description="Content of the artifact")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Metadata associated with the artifact")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(), description="Creation timestamp")
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat(), description="Last updated timestamp")
    status: ArtifactStatus = Field(default=ArtifactStatus.ARCHIVED, description="Current status of the artifact")
    current_version: str = Field(default="", description="Current version of the artifact")
    version_history: list = Field(default_factory=list, description="History of versions for the artifact")
    create_file: bool = Field(default=False, description="Flag to indicate if a file should be created")

    # Use model_validator for initialization logic
    @model_validator(mode='after')
    def setup_artifact(self):
        """Initialize the artifact after validation"""
        # Ensure artifact_id is always a valid string
        if not self.artifact_id:
            self.artifact_id = str(uuid.uuid4())
            
        # Reset status to DRAFT for new artifacts
        if not self.version_history:
            self.status = ArtifactStatus.DRAFT
            # Record initial version
            self._record_version("Initial version")
        
        return self

    def _record_version(self, description: str) -> None:
        """Record current state as a new version"""
        version = {
            "timestamp": datetime.now().isoformat(),
            "description": description,
            "content": self.content,
            "status": self.status
        }
        self.version_history.append(version)
        self.updated_at = version["timestamp"]

    def update_content(self, content: Any, description: str = "Content update") -> None:
        """
        Update artifact content and record version
        
        Args:
            content: New content
            description: Update description
        """
        self.content = content
        self.status = ArtifactStatus.EDITED
        self._record_version(description)

    def update_metadata(self, metadata: Dict[str, Any]) -> None:
        """
        Update artifact metadata
        
        Args:
            metadata: New metadata (will be merged with existing metadata)
        """
        self.metadata.update(metadata)
        self.updated_at = datetime.now().isoformat()

    def mark_complete(self) -> None:
        """Mark the artifact as complete"""
        self.status = ArtifactStatus.COMPLETE
        self._record_version("Marked as complete")

    def archive(self) -> None:
        """Archive the artifact"""
        self.status = ArtifactStatus.ARCHIVED
        self._record_version("Artifact archived")

    def get_version(self, index: int) -> Optional[Dict[str, Any]]:
        """Get version at the specified index"""
        if 0 <= index < len(self.version_history):
            return self.version_history[index]
        return None

    def revert_to_version(self, index: int) -> bool:
        """Revert to a specific version"""
        version = self.get_version(index)
        if version:
            self.content = version["content"]
            self.status = version["status"]
            self._record_version(f"Reverted to version {index}")
            return True
        return False

    def to_dict(self) -> Dict[str, Any]:
        """Convert artifact to dictionary"""
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type.value,
            "content": self.content,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status.name,
            "version_count": len(self.version_history)
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Artifact":
        """Create an artifact instance from a dictionary"""
        artifact_type = ArtifactType(data["artifact_type"])
        artifact = cls(
            artifact_type=artifact_type,
            content=data["content"],
            metadata=data["metadata"],
            artifact_id=data.get("artifact_id", str(uuid.uuid4()))
        )
        artifact.created_at = data["created_at"]
        artifact.updated_at = data["updated_at"]
        artifact.status = ArtifactStatus[data["status"]]

        # If version history exists, restore it as well
        if "version_history" in data:
            artifact.version_history = data["version_history"]

        return artifact