from datetime import datetime
from typing import Optional

import pytz  # Add pytz for timezone handling
from pydantic import BaseModel

from aworld.core.memory import MemoryStore
from aworld.memory.models import (
    MemoryItem, MemoryAIMessage, MemoryHumanMessage, MemorySummary, MemorySystemMessage, MemoryToolMessage,
    MessageMetadata,
    UserProfile, AgentExperience, ConversationSummary, Fact
)
from aworld.models.model_response import ToolCall

try:
    from sqlalchemy.orm import declarative_base
    from sqlalchemy import Column, String, DateTime, Boolean, Integer, Index, cast
    from sqlalchemy.dialects.mysql import JSON
    from sqlalchemy import create_engine, ForeignKey
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import func
    
    Base = declarative_base()
except ImportError as e:
    # Re-raise ImportError to prevent module from loading
    raise ImportError(
        "SQLAlchemy is not installed. Please install it to use MySQLMemoryStore: "
        "pip install sqlalchemy pymysql"
    ) from e

# Get local timezone
LOCAL_TZ = pytz.timezone('Asia/Shanghai')  # Default to China timezone

def to_local_time(dt: datetime) -> str:
    """Convert UTC datetime to local timezone string."""
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    return dt.astimezone(LOCAL_TZ).isoformat()

def from_iso_time(iso_str: str) -> datetime:
    """Convert ISO format string to UTC datetime."""
    if not iso_str:
        return datetime.now(pytz.utc)
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = LOCAL_TZ.localize(dt)
        return dt.astimezone(pytz.utc)
    except ValueError:
        return datetime.now(pytz.utc)

class MemoryItemModel(Base):
    """SQLAlchemy model for memory items."""
    __tablename__ = 'aworld_memory_items'

    id = Column(String(255), primary_key=True)
    content = Column(JSON)  # Using JSON for MySQL
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))
    memory_meta = Column(JSON)  # Renamed from metadata to memory_meta
    tags = Column(JSON)  # Store tags as JSON array in MySQL
    memory_type = Column(String(255))
    version = Column(Integer)
    deleted = Column(Boolean, default=False)

    # Create indexes for MySQL
    __table_args__ = (
        Index('idx_memory_items_type', memory_type),
        Index('idx_memory_items_created', created_at),
        # MySQL 5.7+ supports generated columns for JSON indexing
        # For JSON queries, we'll use functional indexes or JSON_EXTRACT in queries
    )

class MemoryHistoryModel(Base):
    """SQLAlchemy model for memory history."""
    __tablename__ = 'aworld_memory_histories'

    memory_id = Column(String(255), ForeignKey('aworld_memory_items.id'), primary_key=True)
    history_id = Column(String(255), ForeignKey('aworld_memory_items.id'), primary_key=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


def orm_to_memory_item(orm_item: MemoryItemModel) -> Optional[MemoryItem]:
    """Convert ORM model to MemoryItem."""
    if not orm_item:
        return None

    memory_meta = orm_item.memory_meta or {}
    role = memory_meta.get('role')
    message_type = orm_item.memory_type

    # Convert tags from JSON to list if needed
    tags = orm_item.tags
    if tags is None:
        tags = []
    elif not isinstance(tags, list):
        tags = list(tags) if tags else []

    base_data = {
        'id': orm_item.id,
        'created_at': to_local_time(orm_item.created_at),  # Convert to local time
        'updated_at': to_local_time(orm_item.updated_at),  # Convert to local time
        'tags': tags,
        'version': orm_item.version,
        'deleted': orm_item.deleted
    }

    if role == 'system':
        return MemorySystemMessage(
            content=orm_item.content,
            metadata=MessageMetadata(**memory_meta),
            **base_data
        )
    elif role == 'user':
        return MemoryHumanMessage(
            metadata=MessageMetadata(**memory_meta),
            content=orm_item.content,
            **base_data
        )
    elif role == 'assistant':
        tool_calls_jsons = memory_meta.get('tool_calls', [])
        tool_calls = []
        for tool_calls_json in tool_calls_jsons:
            tool_call = ToolCall.from_dict(tool_calls_json)
            tool_calls.append(tool_call)
        return MemoryAIMessage(
            content=orm_item.content,
            tool_calls=tool_calls,
            metadata=MessageMetadata(**memory_meta),
            **base_data
        )
    elif role == 'tool':
        return MemoryToolMessage(
            tool_call_id=memory_meta.get('tool_call_id'),
            content=orm_item.content,
            status=memory_meta.get('status', 'success'),
            metadata=MessageMetadata(**memory_meta),
            **base_data
        )
    elif message_type == 'fact':
        if not orm_item.content:
            return None
        if not isinstance(orm_item.content, dict):
            return None

        return Fact(
            content=orm_item.content,
            user_id=orm_item.memory_meta.get('user_id'),
            metadata=memory_meta,
            **base_data
        )
    elif message_type == 'user_profile':
        if not orm_item.content:
            return None
        if not isinstance(orm_item.content, dict):
            return None


        return UserProfile(
            key=orm_item.content.get('key'),
            value=orm_item.content.get('value'),
            user_id=orm_item.memory_meta.get('user_id'),
            metadata=memory_meta,
            **base_data
        )
    elif message_type == 'agent_experience':
        if not orm_item.content:
            return None
        if not isinstance(orm_item.content, dict):
            return None
        return AgentExperience(
            skill=orm_item.content.get('skill'),
            actions=orm_item.content.get('actions'),
            agent_id=orm_item.memory_meta.get('agent_id'),
            metadata=memory_meta
        )
    elif message_type == 'summary':
        if not orm_item.content:
            return None
        if not isinstance(orm_item.content, str):
            return None
        # Extract item_ids from metadata
        item_ids = memory_meta.get('item_ids', [])
        # Create MessageMetadata from memory_meta
        summary_metadata = MessageMetadata(
            agent_id=memory_meta.get('agent_id'),
            agent_name=memory_meta.get('agent_name'),
            session_id=memory_meta.get('session_id'),
            task_id=memory_meta.get('task_id'),
            user_id=memory_meta.get('user_id')
        )
        return MemorySummary(
            item_ids=item_ids,
            summary=orm_item.content,
            metadata=summary_metadata,
            **base_data
        )
    elif message_type == 'conversation_summary':
        if not orm_item.content:
            return None
        if not isinstance(orm_item.content, str):
            return None
        # Preserve all custom metadata attributes
        conversation_summary_metadata = MessageMetadata(**memory_meta)
        return ConversationSummary(
            user_id=memory_meta.get('user_id'),
            session_id=memory_meta.get('session_id'),
            summary=orm_item.content,
            metadata=conversation_summary_metadata,
            **base_data
        )
    else:
        return MemoryItem(**{
            'id': orm_item.id,
            'content': orm_item.content,
            'created_at': to_local_time(orm_item.created_at),  # Convert to local time
            'updated_at': to_local_time(orm_item.updated_at),  # Convert to local time
            'metadata': memory_meta,  # Map back to metadata for MemoryItem
            'tags': tags,
            'memory_type': orm_item.memory_type,
            'version': orm_item.version,
            'deleted': orm_item.deleted
        })


def memory_item_to_orm(item: MemoryItem) -> MemoryItemModel:
    """Convert MemoryItem to ORM model."""
    # Handle content serialization
    content = item.content
    if isinstance(content, BaseModel):
        content = content.model_dump()  # Use model_dump() instead of model_dump_json() for dict conversion
    
    return MemoryItemModel(
        id=item.id,
        content=content,  # Use serialized content
        created_at=from_iso_time(item.created_at),  # Convert to UTC
        updated_at=from_iso_time(item.updated_at),  # Convert to UTC
        memory_meta=item.metadata,  # Map from metadata to memory_meta
        tags=item.tags,  # Store as JSON array
        memory_type=item.memory_type,
        version=item.version,
        deleted=item.deleted
    )


class MySQLMemoryStore(MemoryStore):
    """
    MySQL implementation of the memory store using SQLAlchemy.
    
    This class provides a MySQL-based storage backend for the memory system,
    implementing all required methods from the MemoryStore interface.
    """
    
    def __init__(
        self, 
        db_url: str,
        pool_size: int = 10,
        max_overflow: int = 20,
        pool_timeout: int = 30,
        pool_recycle: int = 3600,
        pool_pre_ping: bool = True,
        echo: bool = False
    ):
        """
        Initialize MySQL memory store.
        
        Args:
            db_url (str): SQLAlchemy database URL
                Format: mysql+pymysql://user:password@host:port/dbname
                or: mysql+mysqlconnector://user:password@host:port/dbname
            pool_size (int): The number of connections to maintain in the pool.
                Default: 10
            max_overflow (int): The maximum number of connections to allow in
                addition to pool_size. Default: 20
            pool_timeout (int): Number of seconds to wait before giving up on
                getting a connection from the pool. Default: 30
            pool_recycle (int): Number of seconds after which a connection is
                discarded and replaced with a new one. MySQL default wait_timeout
                is 8 hours (28800 seconds), so setting this to 3600 (1 hour) is
                recommended. Default: 3600
            pool_pre_ping (bool): If True, the connection pool will validate
                connections before using them. This ensures connections are
                alive and prevents errors from stale connections. Default: True
            echo (bool): If True, the Engine will log all statements as well as
                a repr() of their parameter lists to the default log handler.
                Default: False
        """
        # Configure connection pool
        pool_args = {
            'pool_size': pool_size,
            'max_overflow': max_overflow,
            'pool_timeout': pool_timeout,
            'pool_recycle': pool_recycle,
            'pool_pre_ping': pool_pre_ping,
        }
        
        self.engine = create_engine(
            db_url, 
            echo=echo, 
            future=True,
            **pool_args
        )
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def _build_filters(self, query, filters: dict = None):
        """Build SQLAlchemy query filters."""
        if not filters:
            return query.filter(MemoryItemModel.deleted == False)

        query = query.filter(MemoryItemModel.deleted == False)
        for key, value in filters.items():
            if value is not None:
                if key in ['user_id', 'agent_id', 'session_id', 'task_id', 'agent_name', 'tool_call_id']:
                    # MySQL JSON query syntax: JSON_EXTRACT returns JSON value
                    # Use JSON_UNQUOTE to extract and unquote the JSON value as text (removes quotes)
                    # For MySQL 5.7+, JSON_UNQUOTE removes quotes from JSON strings
                    json_path = f'$.{key}'
                    json_extract = func.json_extract(MemoryItemModel.memory_meta, json_path)
                    # Use JSON_UNQUOTE to remove quotes, then cast to String for comparison
                    query = query.filter(
                        cast(func.json_unquote(json_extract), String) == str(value)
                    )
                elif key == 'memory_type':
                    # Handle memory_type as a list or single value
                    if isinstance(value, list):
                        query = query.filter(MemoryItemModel.memory_type.in_(value))
                    else:
                        query = query.filter(MemoryItemModel.memory_type == value)
        return query

    def add(self, memory_item: MemoryItem):
        """Add a new memory item to the store."""
        with self.Session() as session:
            orm_item = memory_item_to_orm(memory_item)
            session.add(orm_item)
            session.commit()

    def get(self, memory_id) -> Optional[MemoryItem]:
        """Get a memory item by ID."""
        with self.Session() as session:
            orm_item = session.query(MemoryItemModel).filter_by(
                id=memory_id, deleted=False
            ).first()
            return orm_to_memory_item(orm_item)

    def get_first(self, filters: dict = None) -> Optional[MemoryItem]:
        """Get the first memory item matching the filters."""
        with self.Session() as session:
            query = session.query(MemoryItemModel)
            query = self._build_filters(query, filters)
            orm_item = query.order_by(MemoryItemModel.created_at.asc()).first()
            return orm_to_memory_item(orm_item)

    def total_rounds(self, filters: dict = None) -> int:
        """Get total number of memory rounds matching the filters."""
        with self.Session() as session:
            query = session.query(MemoryItemModel)
            query = self._build_filters(query, filters)
            return query.count()

    def get_all(self, filters: dict = None) -> list[MemoryItem]:
        """Get all memory items matching the filters."""
        with self.Session() as session:
            query = session.query(MemoryItemModel)
            query = self._build_filters(query, filters)
            orm_items = query.order_by(MemoryItemModel.created_at.asc()).all()
            return [orm_to_memory_item(item) for item in orm_items]

    def get_last_n(self, last_rounds: int, filters: dict = None) -> list[MemoryItem]:
        """Get the last N memory rounds matching the filters."""
        with self.Session() as session:
            query = session.query(MemoryItemModel)
            query = self._build_filters(query, filters)
            orm_items = query.order_by(MemoryItemModel.created_at.desc()).limit(last_rounds).all()
            return [orm_to_memory_item(item) for item in reversed(orm_items)]

    def update(self, memory_item: MemoryItem):
        """Update a memory item."""
        with self.Session() as session:
            orm_item = session.query(MemoryItemModel).filter_by(id=memory_item.id).first()
            if orm_item:
                orm_item.content = memory_item.content
                orm_item.created_at = from_iso_time(memory_item.created_at)
                orm_item.updated_at = from_iso_time(memory_item.updated_at)  # Convert to UTC
                orm_item.memory_meta = memory_item.metadata
                orm_item.tags = memory_item.tags
                orm_item.memory_type = memory_item.memory_type
                orm_item.version = memory_item.version
                orm_item.deleted = memory_item.deleted
                session.commit()

    def delete(self, memory_id):
        """Soft delete a memory item."""
        with self.Session() as session:
            orm_item = session.query(MemoryItemModel).filter_by(id=memory_id).first()
            if orm_item:
                orm_item.deleted = True
                orm_item.updated_at = datetime.now(pytz.utc)  # Use UTC time
                session.commit()
    
    def delete_items(self, message_types: list[str], session_id: str, task_id: str, filters: dict = None):
        filters = filters or {}
        filters['memory_type'] = message_types
        filters['session_id'] = session_id
        filters['task_id'] = task_id
        with self.Session() as session:
            query = session.query(MemoryItemModel)
            query = self._build_filters(query, filters)
            query.update({
                MemoryItemModel.deleted: True,
                MemoryItemModel.updated_at: datetime.now(pytz.utc)  # Use UTC time
            })
            session.commit()

    def history(self, memory_id) -> list[MemoryItem] | None:
        """Get the history of a memory item."""
        with self.Session() as session:
            history_items = session.query(MemoryItemModel).join(
                MemoryHistoryModel,
                MemoryHistoryModel.history_id == MemoryItemModel.id
            ).filter(
                MemoryHistoryModel.memory_id == memory_id
            ).order_by(MemoryItemModel.created_at.asc()).all()

            if not history_items:
                return None

            return [orm_to_memory_item(item) for item in history_items]

