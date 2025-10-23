from typing import Any

from ...utils.text_cleaner import clean_web_content
from aworld.output import Artifact, ArtifactType


class SearchArtifact(Artifact):

    @staticmethod
    def create(query: str, title: str, url: str, content: Any, metadata: dict =None, **kwargs) -> "SearchArtifact":
        """
        Create a search artifact

        Args:
            query: The query for the search
            title: The title of the search
            url: The url of the search
            content: The content of the search
        """
        # Clean the content if it's a string (web content)
        if isinstance(content, str):
            cleaned_content = clean_web_content(content)
        else:
            cleaned_content = content
        if not metadata:
            metadata = {}
        return SearchArtifact(content=cleaned_content, artifact_type=ArtifactType.TEXT, metadata={
            'query': query,
            'title': title,
            'url': url,
            'summary_content': SearchArtifact.get_content_summary(cleaned_content),
            **metadata
        })

    @property
    def query(self):
        return self.metadata.get('query')

    @query.setter
    def query(self, query: str):
        if query:
            self.metadata['query'] = query

    @property
    def title(self):
        return self.metadata.get('title')

    @title.setter
    def title(self, title: str):
        if title:
            self.metadata['title'] = title

    @property
    def summary(self):
        return (f"<title>{self.title}</title>\n"
                f"<url>{self.url}</url>\n"
                f"<keywords>{self.url}</keywords>\n"
                f"<abstract>\n{self.metadata.get('summary_content')}\n</abstract>\n")


    @property
    def url(self):
        return self.metadata.get('url')

    @url.setter
    def url(self, url: str):
        if url:
            self.metadata['url'] = url

    @property
    def embedding_text(self):
        return (f"title: {self.title}\n\n"
                   f"url: {self.url}\n\n"
                   f"content: {self.content}")

    @staticmethod
    def get_content_summary(content: str, max_length: int = 250) -> str:
        """Get summary of document content

        Args:
            max_length: Maximum length of summary

        Returns:
            Truncated content with ellipsis if needed
        """
        content = content.strip()
        if len(content) <= max_length:
            return content
        return content[:max_length] + "..."
