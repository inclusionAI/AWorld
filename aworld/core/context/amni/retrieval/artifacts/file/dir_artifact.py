import traceback
from datetime import datetime
from typing import Optional, Dict, Any, List

from pydantic import Field

from aworld.logs.util import logger
from aworld.output.artifact import ArtifactAttachment,Artifact, ArtifactType
from .file_repository import FileRepository, OssFileRepository, LocalFileRepository


class DirArtifact(Artifact):
    base_path: Optional[str] = Field(default='', description="base path for file uploads")
    file_repository: Optional[FileRepository] = Field(default=None, description="file repository", exclude=True)
    inner_attachments: Optional[List[ArtifactAttachment]] = Field(default=None, description="inner attachments", exclude=True)

    def __init__(self, 
                 content: Any = None,
                 metadata: Optional[Dict[str, Any]] = None,
                 file_repository: Optional[FileRepository] = None,
                 base_path: Optional[str] = None,
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
        
        # Initialize file repository (defaults to OSS if not provided)
        self.file_repository = file_repository or OssFileRepository()

    @classmethod
    def with_local_repository(cls, base_path: str, **kwargs) -> 'DirArtifact':
        """Create a DirArtifact with a local file repository."""
        local_repo = LocalFileRepository(base_path)
        return cls(file_repository=local_repo, base_path=base_path, **kwargs)
    
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

    def add_file(self, attachment: ArtifactAttachment) -> bool:
        try:
            if not isinstance(attachment, ArtifactAttachment):
                raise ValueError("attachment must be an instance of ArtifactAttachment")
            
            # Initialize inner_attachments list if it doesn't exist
            if self.inner_attachments is None:
                self.inner_attachments = []
            

            # Update metadata
            self.metadata['attachment_count'] = len(self.inner_attachments)
            self.updated_at = datetime.now().isoformat()
            
            # upload to repository
            self._upload_file_to_repository(attachment)

            # Add the attachment
            if not self.check_attachment_exists(attachment):
                self.inner_attachments.append(attachment)
            return True
            
        except Exception as e:
            logger.error(f"❌ Error adding attachment: {e}")
            logger.debug(f"❌ Traceback: {traceback.format_exc()}")
            return False

    def check_attachment_exists(self, attachment: ArtifactAttachment) -> bool:
        if not self.inner_attachments:
            return False

        for att in self.inner_attachments:
            if att.filename == attachment.filename:
                return True

        return False

    def _upload_file_to_repository(self, attachment: ArtifactAttachment, 
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
            
            # Upload content to file repository
            success = self.file_repository.upload_data(
                key=custom_key,
                data=attachment.content,
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
                
                return custom_key
            else:
                logger.error(f"❌ Failed to upload attachment {attachment.filename} to repository")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error uploading attachment to repository: {e}")
            logger.debug(f"❌ Traceback: {traceback.format_exc()}")
            return None
    
    def get_file(self, filename: str) -> Optional[ArtifactAttachment]:
        if not self.inner_attachments:
            return None
        
        for attachment in self.inner_attachments:
            if attachment.filename == filename:
                return attachment
        
        return None

    def remove_file(self, filename: str) -> bool:
        try:
            if not self.inner_attachments:
                return False

            for i, attachment in enumerate(self.inner_attachments):
                if attachment.filename == filename:
                    del self.inner_attachments[i]
                    self.metadata['attachment_count'] = len(self.inner_attachments)
                    self.updated_at = datetime.now().isoformat()
                    return True

            return False

        except Exception as e:
            logger.error(f"❌ Error removing attachment: {e}")
            return False

    def list_files(self) -> List[Dict[str, Any]]:
        if not self.inner_attachments:
            return []
        
        return [
            {
                'filename': att.filename,
                'mime_type': att.mime_type,
                'path': att.path,
                'repository_key': getattr(att, 'metadata', {}).get('repository_key'),
                'uploaded_at': getattr(att, 'metadata', {}).get('uploaded_at')
            }
            for att in self.inner_attachments
        ]
    
    def reload_working_files(self) -> None:
        """
        Reload working files from the base_path in the remote repository.
        This method queries the file repository for files in the base_path
        and populates the inner_attachments list with the found files.
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
            
            # Clear existing inner_attachments
            if self.inner_attachments is None:
                self.inner_attachments = []
            else:
                self.inner_attachments.clear()
            
            # Convert remote files to ArtifactAttachment objects
            for file_info in remote_files:
                try:
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
                    
                    self.inner_attachments.append(attachment)
                    logger.debug(f"✅ Reload Working Files: {file_info['filename']} ({file_info.get('size', 0)} bytes)")
                    
                except Exception as e:
                    logger.error(f"❌ Reload Working Files: Error processing file {file_info.get('key', 'unknown')}: {e}")
                    continue
            
            # Update metadata
            self.metadata['attachment_count'] = len(self.inner_attachments)
            self.metadata['last_reload'] = datetime.now().isoformat()
            self.updated_at = datetime.now().isoformat()
            
            # logger.info(f"✅ Successfully reloaded {len(self.inner_attachments)} files from {self.base_path}")
        except Exception as e:
            logger.error(f"❌ Error reloading working files: {e}")
            logger.debug(f"❌ Traceback: {traceback.format_exc()}")
            logger.info(f"╰────────────────────────────────────────────────────────────────────────────────────────────────────╯")

    def _get_mime_type(self, filename: str) -> str:
        """Determine MIME type based on file extension."""
        import mimetypes
        mime_type, _ = mimetypes.guess_type(filename)
        return mime_type or 'application/octet-stream'

