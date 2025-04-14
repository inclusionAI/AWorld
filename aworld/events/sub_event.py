"""
Event subscription functionality.
"""
import functools
from typing import Callable, Any, Union, List, Optional, Dict

from aworld.events.event import Event
from aworld.events.pub_event import register_subscriber, unregister_subscriber, SubscriptionType


def sub_event(
    event_code: Union[str, List[str]] = None,
    event_group: Union[str, List[str]] = None,
    event_id: Union[str, List[str]] = None
):
    """
    Decorator for subscribing to events. 
    Supports subscription by event_code, event_group, or event_id.
    
    When multiple parameters are specified, they act as AND conditions.
    For example, @sub_event(event_group="critical")
    will only receive events that have event_group="critical".
    
    If no parameters are specified, subscribes to all events.
    
    Args:
        event_code: Event code(s) to subscribe to
        event_group: Event group(s) to subscribe to
        event_id: Event ID(s) to subscribe to
    
    Returns:
        Decorator function that registers the handler
    """
    def decorator(func: Callable[[Event], Any]):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        
        # Record subscription information (for unsubscribing)
        subscriptions = []
        
        # Check if there are any event parameters
        has_event_params = any(x is not None for x in [event_code, event_group, event_id])
        
        # If no parameters: subscribe to all events
        if not has_event_params:
            register_subscriber(func, SubscriptionType.ALL)
            subscriptions.append((SubscriptionType.ALL, None, None))
        
        # If multiple event parameters: combined condition subscription
        elif sum(1 for p in [event_code, event_group, event_id] if p is not None) > 1:
            conditions = {}
            if event_code is not None:
                conditions['event_code'] = event_code if not isinstance(event_code, list) else event_code[0]
            if event_group is not None:
                conditions['event_group'] = event_group if not isinstance(event_group, list) else event_group[0]
            if event_id is not None:
                conditions['event_id'] = event_id if not isinstance(event_id, list) else event_id[0]
            
            register_subscriber(func, SubscriptionType.COMBINED, combined_conditions=conditions)
            subscriptions.append((SubscriptionType.COMBINED, None, conditions))
        
        # If only one event parameter: single condition subscription
        else:
            if event_code is not None:
                register_subscriber(func, SubscriptionType.EVENT_CODE, event_code)
                subscriptions.append((SubscriptionType.EVENT_CODE, event_code, None))
            elif event_group is not None:
                register_subscriber(func, SubscriptionType.EVENT_GROUP, event_group)
                subscriptions.append((SubscriptionType.EVENT_GROUP, event_group, None))
            elif event_id is not None:
                register_subscriber(func, SubscriptionType.EVENT_ID, event_id)
                subscriptions.append((SubscriptionType.EVENT_ID, event_id, None))
        
        wrapper._subscriptions = subscriptions
        return wrapper
    return decorator


def unsubscribe(func: Callable) -> bool:
    """
    Unsubscribe a decorated function.
    
    Args:
        func: Function to unsubscribe
        
    Returns:
        True if the function was unsubscribed successfully
    """
    if not hasattr(func, '_subscriptions'):
        # If function has no subscription information, can't unsubscribe
        return False
    
    result = False
    for subscription_type, value, combined_conditions in func._subscriptions:
        if subscription_type == SubscriptionType.COMBINED:
            if unregister_subscriber(func, subscription_type, combined_conditions=combined_conditions):
                result = True
        else:
            if unregister_subscriber(func, subscription_type, value):
                result = True
    
    return result 