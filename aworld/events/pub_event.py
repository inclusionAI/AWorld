"""
Event publishing functionality.
"""
from aworld.logs.util import logger
from typing import Dict, List, Callable, Any, Union, Tuple, Set
from enum import Enum

from aworld.events.event import Event

# Define subscription types
class SubscriptionType(Enum):
    EVENT_CODE = "event_code"
    EVENT_GROUP = "event_group"
    EVENT_ID = "event_id"
    COMBINED = "combined"  # New type for combined conditions
    ALL = "all"  # Wildcard subscription, receives all events

# Subscriber registry, formatted as:
# {
#   'event_code': {
#     'code_value1': [handler1, handler2, ...],
#     'code_value2': [handler3, ...],
#   },
#   'event_group': {
#     'group1': [handler6, ...],
#   },
#   'event_id': {
#     'id1': [handler7, ...],
#   },
#   'combined': {
#     'hash1': {
#        'conditions': {'event_code': 'value1', ...},
#        'handlers': [handler8, ...],
#     },
#     'hash2': {...},
#   },
#   'all': [handler11, handler12, ...]
# }
_subscribers: Dict[str, Any] = {
    SubscriptionType.EVENT_CODE.value: {},
    SubscriptionType.EVENT_GROUP.value: {},
    SubscriptionType.EVENT_ID.value: {},
    SubscriptionType.COMBINED.value: {},
    SubscriptionType.ALL.value: []
}


def _generate_combined_hash(conditions: Dict[str, str]) -> str:
    """
    Generate a hash string for combined conditions.
    
    Args:
        conditions: Dictionary of condition name to value
        
    Returns:
        A string hash representing the combined conditions
    """
    # Sort by keys to ensure consistent hash
    sorted_items = sorted(conditions.items())
    return ":".join(f"{k}={v}" for k, v in sorted_items)


def register_subscriber(
    handler: Callable[[Event], Any],
    subscription_type: SubscriptionType,
    value: Union[str, List[str]] = None,
    combined_conditions: Dict[str, Any] = None
) -> None:
    """
    Register a subscriber that can subscribe by event_code, or event_group.
    
    Args:
        handler: Function to call when an event is received
        subscription_type: Subscription type
        value: Value or list of values to subscribe to
        combined_conditions: Dictionary of combined conditions for COMBINED type
    """
    # Global subscription (receives all events)
    if subscription_type == SubscriptionType.ALL:
        _subscribers[SubscriptionType.ALL.value].append(handler)
        return
        
    # Combined conditions subscription
    if subscription_type == SubscriptionType.COMBINED and combined_conditions:
        # Generate hash for combined conditions
        conditions_hash = _generate_combined_hash(combined_conditions)
        combined = _subscribers[SubscriptionType.COMBINED.value]
        
        # If conditions hash doesn't exist, create new entry
        if conditions_hash not in combined:
            combined[conditions_hash] = {
                'conditions': combined_conditions,
                'handlers': []
            }
        
        # Add handler to combined conditions (avoid duplicates)
        if handler not in combined[conditions_hash]['handlers']:
            combined[conditions_hash]['handlers'].append(handler)
        return

    # Simple condition subscription (event_code, event_group, event_id)
    # Handle enum values
    def normalize_value(v):
        return v.name if isinstance(v, Enum) else v

    # Handle multiple values
    if isinstance(value, list):
        for v in value:
            normalized = normalize_value(v)
            _register_single_value(handler, subscription_type, normalized)
    else:
        normalized = normalize_value(value)
        _register_single_value(handler, subscription_type, normalized)


def _register_single_value(handler: Callable, subscription_type: SubscriptionType, value: str) -> None:
    """
    Register a subscriber for a single value.
    
    Args:
        handler: Handler function
        subscription_type: Subscription type
        value: Subscription value
    """
    subscriptions = _subscribers[subscription_type.value]
    
    if value not in subscriptions:
        subscriptions[value] = []
        
    if handler not in subscriptions[value]:
        subscriptions[value].append(handler)


def unregister_subscriber(
    handler: Callable[[Event], Any],
    subscription_type: SubscriptionType,
    value: Union[str, List[str]] = None,
    combined_conditions: Dict[str, Any] = None
) -> bool:
    """
    Unregister a subscriber.
    
    Args:
        handler: Function to unregister
        subscription_type: Subscription type
        value: Value or list of values to unsubscribe from
        combined_conditions: Dictionary of combined conditions for COMBINED type
        
    Returns:
        True if unregistration was successful, False otherwise
    """
    # If wildcard subscription
    if subscription_type == SubscriptionType.ALL:
        if handler in _subscribers[SubscriptionType.ALL.value]:
            _subscribers[SubscriptionType.ALL.value].remove(handler)
            return True
        return False
        
    # If combined conditions
    if subscription_type == SubscriptionType.COMBINED and combined_conditions:
        conditions_hash = _generate_combined_hash(combined_conditions)
        combined = _subscribers[SubscriptionType.COMBINED.value]
        
        if conditions_hash in combined and handler in combined[conditions_hash]['handlers']:
            combined[conditions_hash]['handlers'].remove(handler)
            
            # Clean up empty entries
            if not combined[conditions_hash]['handlers']:
                del combined[conditions_hash]
                
            return True
        return False

    # Handle enum values
    def normalize_value(v):
        return v.name if isinstance(v, Enum) else v
        
    # Handle multiple values
    if isinstance(value, list):
        result = False
        for v in value:
            normalized = normalize_value(v)
            if _unregister_single_value(handler, subscription_type, normalized):
                result = True
        return result
    else:
        normalized = normalize_value(value)
        return _unregister_single_value(handler, subscription_type, normalized)


def _unregister_single_value(handler: Callable, subscription_type: SubscriptionType, value: str) -> bool:
    """
    Unregister a subscriber for a single value.
    
    Args:
        handler: Handler function
        subscription_type: Subscription type
        value: Subscription value
        
    Returns:
        True if unregistration was successful, False otherwise
    """
    subscriptions = _subscribers[subscription_type.value]
    
    if value in subscriptions and handler in subscriptions[value]:
        subscriptions[value].remove(handler)
        # Clean up empty lists
        if not subscriptions[value]:
            del subscriptions[value]
        return True
    return False


def publish_event(event: Event) -> None:
    """
    Publish an event to all relevant subscribers.
    
    Any handler that has subscribed to any of the following will receive the event:
    - The specific event_code
    - The event's event_group
    - The event's event_id (if specified)
    - Combined conditions that match all specified criteria
    - All events (wildcard)
    
    Args:
        event: The event to publish
    """
    handlers_called = set()  # Track which handlers have been called to avoid duplicates
    
    # 1. Process combined conditions - subscriptions with multiple constraints
    _call_handlers_for_combined_conditions(event, handlers_called)
    
    # 2. Process specific event code subscriptions
    if hasattr(event, 'event_code') and event.event_code:
        _call_handlers_for_subscription(
            event, 
            SubscriptionType.EVENT_CODE, 
            event.event_code, 
            handlers_called
        )
    
    # 3. Process event group subscriptions
    if hasattr(event, 'event_group') and event.event_group:
        _call_handlers_for_subscription(
            event, 
            SubscriptionType.EVENT_GROUP, 
            event.event_group, 
            handlers_called
        )
    
    # 4. Process specific event ID subscriptions
    if hasattr(event, 'event_id'):
        _call_handlers_for_subscription(
            event,
            SubscriptionType.EVENT_ID,
            event.event_id,
            handlers_called
        )
    
    # 5. Finally process wildcard subscriptions - these receive all events
    for handler in _subscribers[SubscriptionType.ALL.value]:
        if handler not in handlers_called:
            try:
                handler(event)
                handlers_called.add(handler)
            except Exception as e:
                logger.exception(f"Error handling wildcard event: {e}")


def _call_handlers_for_subscription(
    event: Event,
    subscription_type: SubscriptionType, 
    value: str,
    handlers_called: set
) -> None:
    """
    Call all handlers for a specific subscription type and value.
    
    Args:
        event: Event to handle
        subscription_type: Subscription type
        value: Subscription value
        handlers_called: Set of handlers that have already been called
    """
    # Get all subscriptions for the respective subscription type
    subscriptions = _subscribers[subscription_type.value]
    
    # Check if there are any subscriptions matching the current value
    if value not in subscriptions:
        return
    
    # Process all matching subscriptions
    for handler in subscriptions[value]:
        # Skip if handler has already been called
        if handler in handlers_called:
            continue
            
        # Execute the handler
        try:
            handler(event)
            handlers_called.add(handler)
        except Exception as e:
            logger.exception(f"Error handling event ({subscription_type.value}={value}): {e}")


def _call_handlers_for_combined_conditions(
    event: Event,
    handlers_called: set
) -> None:
    """
    Call all handlers for combinations of multiple conditions.
    
    Args:
        event: Event to handle
        handlers_called: Set of handlers that have already been called
    """
    # Get combined condition subscriptions
    subscriptions = _subscribers[SubscriptionType.COMBINED.value]
    
    # Check each combined subscription
    for conditions_hash, subscription in subscriptions.items():
        conditions = subscription['conditions']
        handlers = subscription['handlers']
        
        # Check if all conditions match
        match_all = True
        for c_type, c_value in conditions.items():
            # Get the event's value for this type
            event_value = getattr(event, c_type, None)
            
            # If event doesn't have this attribute or values don't match, conditions are not met
            if event_value is None or event_value != c_value:
                match_all = False
                break
        
        # If all conditions match, process all handlers in this group
        if match_all:
            for handler in handlers:
                # Skip handlers that have already been called
                if handler in handlers_called:
                    continue
                
                # Call the handler
                try:
                    handler(event)
                    handlers_called.add(handler)
                except Exception as e:
                    logger.exception(f"Error handling event (combined conditions): {e}")


def clear_subscribers(
    subscription_type: SubscriptionType = None,
    value: str = None
) -> None:
    """
    Clear subscribers of a specific type, or all subscribers.
    
    Args:
        subscription_type: Subscription type to clear, or None for all
        value: Specific subscription value, or None for all values of the type
    """
    global _subscribers
    
    if subscription_type is None:
        # Clear all subscribers
        _subscribers = {
            SubscriptionType.EVENT_CODE.value: {},
            SubscriptionType.EVENT_GROUP.value: {},
            SubscriptionType.EVENT_ID.value: {},
            SubscriptionType.COMBINED.value: {},
            SubscriptionType.ALL.value: []
        }
    elif subscription_type == SubscriptionType.ALL:
        # Clear all wildcard subscribers
        _subscribers[SubscriptionType.ALL.value] = []
    elif subscription_type == SubscriptionType.COMBINED:
        # Clear all combined subscribers
        _subscribers[SubscriptionType.COMBINED.value] = {}
    elif value is None:
        # Clear all subscribers of a specific type
        _subscribers[subscription_type.value] = {}
    else:
        # Clear subscribers for a specific type and value
        if value in _subscribers[subscription_type.value]:
            del _subscribers[subscription_type.value][value] 