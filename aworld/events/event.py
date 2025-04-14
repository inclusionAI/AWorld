"""
Base Event class definition.
"""
import uuid
import time
import json
from typing import Dict, Any, Optional


class Event:
    """
    Base Event class that can be inherited by other event classes.
    
    Attributes:
        event_id (str): Unique event identifier
        trace_id (str): Identifier for tracing related events
        event_code (str): Event code representing a specific event, required
        event_group (str): Group this event belongs to
        session_id (str): Session identifier
        query_id (str): Query identifier
        timestamp (float): Event creation timestamp
        data (dict): Additional event data
    """
    
    def __init__(
        self,
        event_code: str,
        event_group: Optional[str] = None,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        query_id: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        **kwargs
    ):
        """
        Initialize a new event.
        
        Args:
            event_code: Event code representing a specific event, required
            event_group: Event group
            trace_id: Tracing identifier, generates one if not provided
            session_id: Session identifier
            query_id: Query identifier
            data: Additional event data
            **kwargs: Additional attributes to set on the event
        """
        if not event_code:
            raise ValueError("event_code cannot be empty")
            
        self.event_id = str(uuid.uuid4())
        self.trace_id = trace_id or str(uuid.uuid4())
        self.event_code = event_code
        self.event_group = event_group
        self.session_id = session_id
        self.query_id = query_id
        self.timestamp = time.time()
        self.data = data or {}
        
        # Add any additional attributes passed via kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert the event to a dictionary.
        
        Returns:
            Dictionary representation of the event
        """
        result = {
            'event_id': self.event_id,
            'trace_id': self.trace_id,
            'event_code': self.event_code,
            'timestamp': self.timestamp,
        }
        
        # Add optional attributes if they exist
        if self.event_group:
            result['event_group'] = self.event_group
        if self.session_id:
            result['session_id'] = self.session_id
        if self.query_id:
            result['query_id'] = self.query_id
        if self.data:
            result['data'] = self.data
            
        # Add any custom attributes (excluding special/private ones)
        for attr in dir(self):
            if (not attr.startswith('_') and 
                attr not in result and 
                attr not in ('to_dict', 'to_json', 'data') and
                not callable(getattr(self, attr))):
                result[attr] = getattr(self, attr)
                
        return result
    
    def to_json(self) -> str:
        """
        Convert the event to a JSON string.
        
        Returns:
            JSON string representation of the event
        """
        return json.dumps(self.to_dict()) 