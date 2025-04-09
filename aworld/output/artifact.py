import uuid
from datetime import datetime
from enum import Enum, auto
from typing import Dict, Any, Optional

from aworld.output.agent_output import AgentOutput


class ArtifactType(Enum):
    """Defines supported artifact types"""
    DIR = "dir"
    TEXT = "text"
    CODE = "code"
    MARKDOWN = "markdown"
    HTML = "html"
    SVG = "svg"
    JSON = "json"
    CSV = "csv"
    TABLE = "table"
    CHART = "chart"
    DIAGRAM = "diagram"
    CUSTOM = "custom"



class ArtifactStatus(Enum):
    """Artifact status"""
    DRAFT = auto()      # Draft status
    COMPLETE = auto()   # Completed status
    EDITED = auto()     # Edited status
    ARCHIVED = auto()   # Archived status

class Artifact(AgentOutput):
    """
    Represents a specific content generation result (artifact)
    
    Artifacts are the basic units of Artifacts technology, representing a structured content unit
    Can be code, markdown, charts, and various other formats
    """

    def __init__(
        self,
        artifact_type: ArtifactType,
        content: Any,
        metadata: Optional[Dict[str, Any]] = None,
        artifact_id: Optional[str] = None,
        render_type: Optional[str] = None,
        create_file: bool = False,
        **kwargs
    ):
        self.artifact_id = artifact_id or str(uuid.uuid4())
        self.artifact_type = artifact_type
        self.content = content
        self.metadata = metadata or {}
        self.render_type = render_type or artifact_type.value
        self.created_at = datetime.now().isoformat()
        self.updated_at = self.created_at
        self.status = ArtifactStatus.DRAFT
        self.version_history = []
        self.create_file = create_file

        # Record initial version
        self._record_version("Initial version")

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
            "render_type": self.render_type,
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
            artifact_id=data["artifact_id"],
            render_type=data["render_type"]
        )
        artifact.created_at = data["created_at"]
        artifact.updated_at = data["updated_at"]
        artifact.status = ArtifactStatus[data["status"]]

        # If version history exists, restore it as well
        if "version_history" in data:
            artifact.version_history = data["version_history"]

        return artifact