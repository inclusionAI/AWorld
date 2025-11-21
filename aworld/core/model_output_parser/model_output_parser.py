# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
from typing import TypeVar, Generic, Any, Dict, List, Optional

from aworld.core.model_output_parser.base_content_parser import BaseContentParser
from aworld.logs.util import logger

INPUT = TypeVar('INPUT')
OUTPUT = TypeVar('OUTPUT')


class ModelOutputParser(Generic[INPUT, OUTPUT]):
    """Model output parser."""
    __metaclass__ = abc.ABCMeta

    def __init__(self, parsers: List[BaseContentParser] = None) -> None:
        self._parsers: Dict[str, BaseContentParser] = {}
        # Register parsers when initializing
        if parsers:
            for parser in parsers:
                self.register_parser(parser)

    def register_parser(self, parser: BaseContentParser) -> None:
        """Register a new parser"""
        self._parsers[parser.parser_type] = parser

    def get_parser(self, parser_type: str) -> Optional[BaseContentParser]:
        """Get a parser by type"""
        return self._parsers.get(parser_type)

    def get_parsers(self) -> Dict[str, BaseContentParser]:
        """Get all parsers"""
        return self._parsers

    @abc.abstractmethod
    async def parse(self, content: INPUT, **kwargs) -> OUTPUT:
        """Parse the content to the OUTPUT format."""
