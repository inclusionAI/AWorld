import traceback
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

from pydantic import Field

from aworld.logs.util import logger
from aworld.output.artifact import ArtifactAttachment,Artifact, ArtifactType
from .file_repository import FileRepository, OssFileRepository, LocalFileRepository
from .utils import FileUtils


class DirArtifact(Artifact):
    base_path: Optional[str] = Field(default='', description="base path for file uploads")
    file_repository: Optional[FileRepository] = Field(default=None, description="file repository", exclude=True)
    mount_path: Optional[str] = Field(default='', description="mount path for file uploads")

    def __init__(self, 
                 content: Any = None,
                 metadata: Optional[Dict[str, Any]] = None,
                 file_repository: Optional[FileRepository] = None,
                 base_path: Optional[str] = None,
                 mount_path: Optional[str] = None,
                 **kwargs):
        # Set default artifact type to DIR for file artifacts
        artifact_type = kwargs.get('artifact_type', ArtifactType.DIR)
        
        # Initialize base Artifact
        super().__init__(
            artifact_type=artifact_type,
            content=content,
            metadata=metadata or {},
            **kwargs
        )
        
        # Set base path for file uploads
        self.base_path = base_path or ""

        self.mount_path = mount_path or base_path or ""
        
        # Initialize file repository (defaults to OSS if not provided)
        self.file_repository = file_repository or OssFileRepository()

    @classmethod
    def with_local_repository(cls, base_path: str, **kwargs) -> 'DirArtifact':
        """Create a DirArtifact with a local file repository."""
        local_repo = LocalFileRepository(base_path)
        return cls(file_repository=local_repo, base_path=base_path, mount_path=base_path, **kwargs)
    
    @classmethod
    def with_oss_repository(cls, 
                           access_key_id: Optional[str] = None,
                           access_key_secret: Optional[str] = None,
                           endpoint: Optional[str] = None,
                           bucket_name: Optional[str] = None,
                           base_path: Optional[str] = None,
                           **kwargs) -> 'DirArtifact':
        """Create a DirArtifact with an OSS file repository."""
        oss_repo = OssFileRepository(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            endpoint=endpoint,
            bucket_name=bucket_name
        )
        return cls(file_repository=oss_repo, base_path=base_path, **kwargs)

    async def add_file(self, attachment: ArtifactAttachment) -> Tuple[bool, Optional[str], Optional[str]]:
        try:
            if not isinstance(attachment, ArtifactAttachment):
                raise ValueError("attachment must be an instance of ArtifactAttachment")
            
            # Initialize attachments list if it doesn't exist
            if self.attachments is None:
                self.attachments = []
            

            # Update metadata
            self.metadata['attachment_count'] = len(self.attachments)
            self.updated_at = datetime.now().isoformat()
            
            # upload to repository
            await self._upload_file_to_repository(attachment)

            # Add the attachment
            if not self.check_attachment_exists(attachment):
                self.attachments.append(attachment)

            if isinstance(attachment.content, str):
               return True, attachment.path,attachment.content
            else:
                return True, attachment.path,None
            
        except Exception as e:
            logger.error(f"❌ Error adding attachment: {e}")
            logger.debug(f"❌ Traceback: {traceback.format_exc()}")
            return False, None, None

    def check_attachment_exists(self, attachment: ArtifactAttachment) -> bool:
        if not self.attachments:
            return False

        for att in self.attachments:
            if att.filename == attachment.filename:
                return True

        return False

    async def _upload_file_to_repository(self, attachment: ArtifactAttachment,
                                  custom_key: Optional[str] = None) -> Optional[str]:
        try:
            if not self.file_repository:
                logger.error("❌ File repository not initialized")
                return None
            
            if not isinstance(attachment, ArtifactAttachment):
                raise ValueError("attachment must be an instance of ArtifactAttachment")
            
            # Generate key if not provided
            if custom_key is None:
                # Use base_path, artifact_id and filename to create unique key
                if self.base_path:
                    custom_key = f"{self.base_path}/{attachment.filename}"
                else:
                    custom_key = f"{attachment.filename}"

            # Download content from original source
            content = attachment.content
            if not content and attachment.origin_type and attachment.origin_path:
                content = await FileUtils.read_origin_file_content(attachment.origin_type, attachment.origin_path, attachment.filename)
            
            # Upload content to file repository
            success, file_path = self.file_repository.upload_data(
                key=custom_key,
                data=content,
                metadata={
                    'filename': attachment.filename,
                    'mime_type': attachment.mime_type,
                    'path': attachment.path,
                    'artifact_id': self.artifact_id
                }
            )
            
            if success:
                # Update artifact metadata
                self.metadata['last_attachment_upload'] = datetime.now().isoformat()
                self.updated_at = datetime.now().isoformat()
                attachment.path = file_path
                
                return custom_key
            else:
                logger.error(f"❌ Failed to upload attachment {attachment.filename} to repository")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error uploading attachment to repository: {e}")
            logger.debug(f"❌ Traceback: {traceback.format_exc()}")
            return None


    
    def get_file(self, filename: str) -> Optional[ArtifactAttachment]:
        if not self.attachments:
            return None
        
        for attachment in self.attachments:
            if attachment.filename == filename:
                return attachment
        
        return None

    def remove_file(self, filename: str) -> bool:
        try:
            if not self.attachments:
                return False

            for i, attachment in enumerate(self.attachments):
                if attachment.filename == filename:
                    del self.attachments[i]
                    self.metadata['attachment_count'] = len(self.attachments)
                    self.updated_at = datetime.now().isoformat()
                    return True

            return False

        except Exception as e:
            logger.error(f"❌ Error removing attachment: {e}")
            return False

    def list_files(self) -> List[Dict[str, Any]]:
        if not self.attachments:
            return []
        
        return [
            {
                'filename': att.filename,
                'path': f"{self.base_path}/{att.filename}"
            }
            for att in self.attachments
        ]
    
    def reload_working_files(self) -> None:
        """
        Reload working files from the base_path in the remote repository.
        This method queries the file repository for files in the base_path
        and populates the attachments list with the found files.
        """
        try:
            if not self.file_repository:
                logger.error("❌ Reload Working Files: File repository not initialized")
                return
            
            if not self.base_path:
                logger.error("❌ Reload Working Files: Base path not set")
                return
            
            # logger.info(f"🔄 Starting to reload working files from {self.base_path}")
            
            # List files from the repository using base_path as prefix
            remote_files = self.file_repository.list_files(prefix=self.base_path)
            
            logger.debug(f"📁 Reload Working Files: Found {len(remote_files)} files in repository")
            
            # Clear existing attachments
            if self.attachments is None:
                self.attachments = []
            else:
                self.attachments.clear()
            
            # Convert remote files to ArtifactAttachment objects
            for file_info in remote_files:
                try:
                    # remove hidden files
                    if file_info['filename'].startswith("."):
                        continue
                    # Read file content from repository
                    file_content = self.file_repository.read_data(file_info['key'])
                    if file_content is None:
                        logger.warning(f"⚠️ Reload Working Files: Could not read content for file {file_info['key']}")
                        continue
                    
                    # Determine MIME type based on file extension
                    mime_type = self._get_mime_type(file_info['filename'])
                    
                    # Create ArtifactAttachment
                    attachment = ArtifactAttachment(
                        path=file_info['key'],
                        filename=file_info['filename'],
                        mime_type=mime_type,
                        content=file_content,
                        metadata={
                            'repository_key': file_info['key'],
                            'size': file_info.get('size', 0),
                            'modified_time': file_info.get('modified_time', 0),
                            'reloaded_at': datetime.now().isoformat()
                        }
                    )
                    
                    self.attachments.append(attachment)
                    logger.debug(f"✅ Reload Working Files: {file_info['filename']} ({file_info.get('size', 0)} bytes)")
                    
                except Exception as e:
                    logger.error(f"❌ Reload Working Files: Error processing file {file_info.get('key', 'unknown')}: {e}")
                    continue
            
            # Update metadata
            self.metadata['attachment_count'] = len(self.attachments)
            self.metadata['last_reload'] = datetime.now().isoformat()
            self.updated_at = datetime.now().isoformat()
            
            # logger.info(f"✅ Successfully reloaded {len(self.attachments)} files from {self.base_path}")
        except Exception as e:
            logger.error(f"❌ Error reloading working files: {e}")
            logger.debug(f"❌ Traceback: {traceback.format_exc()}")
            logger.info(f"╰────────────────────────────────────────────────────────────────────────────────────────────────────╯")

    def _get_mime_type(self, filename: str) -> str:
        """Determine MIME type based on file extension."""
        import mimetypes
        mime_type, _ = mimetypes.guess_type(filename)
        return mime_type or 'application/octet-stream'

    def to_dict(self, exclude_content: bool = False) -> Dict[str, Any]:
        """Convert DirArtifact to dictionary."""

        self.reload_working_files()
        result = super().to_dict(exclude_content=exclude_content)
        if not exclude_content:
            result["files"] = self.list_files()
            result["version_count"] = len(self.version_history)
        return result

    def need_save_attachment(self):
        return False

