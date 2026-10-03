# coding: utf-8
# Copyright (c) 2025 inclusionAI.

# Avoid circular import: Use lazy import for ApplicationContext
# Import directly from aworld.core.context.amni when needed

__all__ = ['Context', 'ContextEntry', 'FullHistoryPolicy', 'BudgetPolicy', 'ContextBudget', 'ApplicationContext']

def __getattr__(name):
    if name in ('BudgetPolicy', 'ContextBudget'):
        from aworld.core.context.budget import BudgetPolicy, ContextBudget
        return {'BudgetPolicy': BudgetPolicy, 'ContextBudget': ContextBudget}[name]
    if name == 'Context':
        from aworld.core.context.simple import Context
        return Context
    if name in ('ContextEntry', 'FullHistoryPolicy'):
        from aworld.core.context.simple import ContextEntry, FullHistoryPolicy
        return {'ContextEntry': ContextEntry, 'FullHistoryPolicy': FullHistoryPolicy}[name]
    if name == 'ApplicationContext':
        from aworld.core.context.amni import ApplicationContext
        return ApplicationContext
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
