import uuid
from typing import Any, Optional, Dict, List

from pydantic import Field

from aworld.output.artifact import Artifact, ArtifactType, ArtifactAttachment

CODE_FILE_EXTENSION_MAP = {
                "python": "py",
                "java": "java",
                "javascript": "js",
                "typescript": "ts",
                "html": "html",
                "css": "css",
                "c": "c",
                "cpp": "cpp",
                "csharp": "cs",
                "go": "go",
                "rust": "rs",
                "ruby": "rb",
                "php": "php",
                "swift": "swift",
                "kotlin": "kt",
                "scala": "scala",
                "markdown": "md",
                "txt": "txt",
                "shell": "sh",
                "bash": "sh",
                "sh": "sh",
                "zsh": "zsh",
                "powershell": "ps1",
                "cmd": "cmd",
                "bat": "bat"
            }


class CodeArtifact(Artifact):
    code_interceptor: Any = Field(default=None, description="code executor type")

    def __init__(self, artifact_type: ArtifactType, content: Any, code_type: Optional[str], code_version: Optional[str],
                 code_interceptor_provider: Optional[str] = None,
                 artifact_id: Optional[str] = None, render_type: Optional[str] = None, **kwargs):
        # Extract filename from the first line of the content
        filename = self.extract_filename(content)
        
        # Initialize metadata, including any passed in kwargs
        metadata = {
            "code_type": code_type,
            "code_version": code_version,
            "code_interceptor_provider": code_interceptor_provider,
            "filename": filename  # Store filename in metadata
        }
        
        # Merge additional metadata from kwargs if provided
        if 'metadata' in kwargs:
            metadata.update(kwargs['metadata'])
            del kwargs['metadata']  # Remove metadata from kwargs to avoid multiple values

        super().__init__(
            artifact_type=artifact_type,
            content=content,
            metadata=metadata,
            artifact_id=artifact_id,
            render_type=render_type,
            **kwargs
        )
        self.archive()
        self.code_interceptor = self.init_code_interceptor(code_interceptor_provider)

    @staticmethod
    def extract_filename(content: Any) -> Optional[str]:
        """Extract filename from the first line of the code block comment."""
        if isinstance(content, str):
            lines = content.splitlines()
            if lines:
                first_line = lines[0].strip()
                # Check if the first line is a shebang for bash or other interpreters
                if first_line in ["# /bin/bash", "#!/bin/bash", "#!/usr/bin/env bash", 
                                   "#!/bin/sh", "#!/usr/bin/env python", 
                                   "#!/usr/bin/env python3"]:
                    return None  # Do not return a filename
                # Check for common comment styles in various languages
                if first_line.startswith("#"):  # Python, Ruby, Shell
                    return first_line[1:].strip()  # Remove the comment symbol
                elif first_line.startswith("//"):  # Java, JavaScript, C, C++
                    return first_line[2:].strip()  # Remove the comment symbol
                elif first_line.startswith("/*") and "*/" in first_line:  # C, C++
                    return first_line.split("*/")[0][2:].strip()  # Remove comment symbols
                elif first_line.startswith("<!--"):  # HTML
                    return first_line[4:].strip()  # Remove the comment symbol
                # Add more languages as needed
        return None  # Return None if filename is unknown

    @classmethod
    def build_artifact(cls,
                       content: Any,
                       code_type: Optional[str] = None,
                       code_version: Optional[str] = None,
                       code_interceptor_provider: Optional[str] = None,
                       artifact_id: Optional[str] = None,
                       render_type: Optional[str] = None,
                       **kwargs) -> "CodeArtifact":

        # Create CodeArtifact instance
        if code_type in ['shell', 'sh', 'bash', 'zsh']:
            return ShellArtifact(
                artifact_type=ArtifactType.CODE,
                content=content,
                code_version=code_version,
                code_interceptor_provider=code_interceptor_provider,
                artifact_id=artifact_id,
                render_type=render_type,
                **kwargs
            )
        elif code_type in ['html']:
            return HtmlArtifact(
                content=content,
                artifact_id=artifact_id,
                **kwargs
            )

        return cls(
            artifact_type=ArtifactType.CODE,
            content=content,
            code_type=code_type,
            code_version=code_version,
            code_interceptor_provider=code_interceptor_provider,
            artifact_id=artifact_id,
            render_type=render_type,
            **kwargs
        )

    @classmethod
    def from_code_content(cls, artifact_type: ArtifactType,
                          content: Any,
                          render_type: Optional[str] = None,
                          **kwargs) -> List["CodeArtifact"]:
        code_blocks = cls.extract_model_output_to_code_content(content)  # Extract code blocks
        artifacts = []  # List to store CodeArtifact instances

        for block in code_blocks:
            code_type = block['language']
            code_version = "1.0"

            if code_type in ['python', 'javascript', 'java']:
                code_interceptor_provider = "default_interceptor"
            elif code_type in ['shell', 'sh', 'bash', 'zsh']:
                code_interceptor_provider = "shell_interceptor"
            else:
                code_interceptor_provider = "generic_interceptor"

            artifact = cls.create_artifact(
                artifact_type=ArtifactType.CODE,
                content=block['content'],
                code_type=code_type,
                code_version=code_version,
                code_interceptor_provider=code_interceptor_provider,
                artifact_id=block['artifact_id'],  # Use extracted artifact_id
                render_type=render_type,
                **kwargs
            )
            artifacts.append(artifact)  # Add to the list

        return artifacts  # Return the list of CodeArtifact instances

    def init_code_interceptor(self, code_interceptor_provider):
        pass

    @classmethod
    def extract_model_output_to_code_content(cls, content):
        """
        Extract code blocks from markdown content using mistune.
        
        First extracts all code blocks enclosed in triple backticks,
        then determines the language for each block.
        """

        try:
            import mistune
        except ImportError:
            # install mistune
            import subprocess
            subprocess.run(["pip", "install", "mistune>=3.0.0"], check=True)
            import mistune

        code_blocks = []
        
        #
        extracted_blocks = []
        
        # create custom Render
        class CustomRenderer(mistune.HTMLRenderer):
            def block_code(self, code, info=None):
                language = info.split()[0] if info else 'unknown'
                extracted_blocks.append({
                    "content": code,
                    "language": language
                })
                return ""

        # create Markdown render
        renderer = CustomRenderer()
        markdown = mistune.create_markdown(
            renderer=renderer
        )

        # resolve markdown
        markdown(content)
        
        # process codeblocks
        for block in extracted_blocks:
            artifact_id = str(uuid.uuid4())
            language = block['language']
            file_suffix = CODE_FILE_EXTENSION_MAP.get(language, "txt")
            
            code_blocks.append({
                "artifact_id": artifact_id,
                "content": block['content'],
                "language": language,
                "file_suffix": file_suffix
            })
        
        return code_blocks


class ShellArtifact(CodeArtifact):
    shell_result: str = Field(default="", description="shell execution result")

    def __init__(self, artifact_type: ArtifactType, content: Any, code_version: str,
                 code_interceptor_provider: Optional[str] = None,
                 artifact_id: Optional[str] = None, render_type: Optional[str] = None,
                 shell_result: str = "", **kwargs):

        code_type = "shell"

        # extract filename
        filename = self.extract_filename(content)
        
        # default set terminal.txt
        if not filename:
            filename = "terminal.txt"

        # update metadata
        metadata = kwargs.get('metadata', {})
        metadata['filename'] = filename

        # setting code_interceptor_provider
        if code_interceptor_provider is None:
            code_interceptor_provider = "shell_interceptor"

        super().__init__(artifact_type, content, code_type, code_version,
                         code_interceptor_provider, artifact_id, render_type, metadata=metadata, **kwargs)
        self.shell_result = shell_result

    def execute(self):
        # todo add
        pass

class HtmlArtifact(CodeArtifact):

    def __init__(self, content: Any, artifact_id: Optional[str] = None, **kwargs):
        # Remove artifact_type from kwargs if it exists to avoid conflicts
        kwargs.pop('artifact_type', None)

        super().__init__(
            artifact_type=ArtifactType.HTML,
            content=content,
            code_type='html',
            code_version='1.0',
            artifact_id=artifact_id,
            **kwargs
        )
        content = content.replace("```html", "").replace("```", "")
        self.content = None
        self.attachments.append(
            ArtifactAttachment(filename=f"{artifact_id}.html", content=content, mime_type="text/html")
        )
