# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.logs.util import logger


class InheritanceSingleton(object):
    _instance = {}

    @staticmethod
    def __get_base_class(clazz):
        if clazz == object:
            return None
        bases = clazz.__bases__
        for base in bases:
            if base == InheritanceSingleton:
                return clazz
            else:
                base_class = InheritanceSingleton.__get_base_class(base)
                if base_class:
                    return base_class
        return None

    def __new__(cls, *args, **kwargs):
        base = InheritanceSingleton.__get_base_class(cls)
        if base is None:
            raise ValueError("Singleton base not found")
        if base not in cls._instance:
            cls._instance[base] = super(InheritanceSingleton, cls).__new__(cls)
        else:
            logger.warning(f"{base} has been created!")
        return cls._instance[base]

    @classmethod
    def clear_singleton(cls):
        base = InheritanceSingleton.__get_base_class(cls)
        cls._instance.pop(base, None)
