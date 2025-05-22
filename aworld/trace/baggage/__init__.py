from uuid import uuid4
from contextvars import ContextVar, Token

_BAGGAGE_KEY = "aworld.baggage." + str(uuid4())

_BAGGAGE_CONTEXT = ContextVar(_BAGGAGE_KEY, default=None)


class BaggageContext:
    """
    Baggage context.
    """

    @staticmethod
    def get_baggage() -> dict:
        """
        Get the baggage.
        Returns:
            A dict of baggage.
        """
        baggage = _BAGGAGE_CONTEXT.get()
        if isinstance(baggage, dict):
            return baggage
        return {}

    @staticmethod
    def get_baggage_value(key: str):
        """
        Get the value for a key from baggage.
        Args:
            key: The key of the value to retrieve.
        Returns:
            The baggage value.
        """
        baggage = BaggageContext.get_baggage()
        if key:
            return baggage.get(key)
        return None

    @staticmethod
    def set_baggage(key: str, value: object):
        """
        Set the value for a key in baggage.
        Args:
            key: The key of the value to set.
            value: The value to set.
        """
        baggage = BaggageContext.get_baggage().copy()
        baggage[key] = value
        _BAGGAGE_CONTEXT.set(baggage)
