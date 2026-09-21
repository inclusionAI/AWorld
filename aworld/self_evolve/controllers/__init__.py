"""Dependency-directed phase controllers for self-evolve orchestration.

Controllers are imported from their defining modules so loading one phase does
not eagerly load the dependencies of every other phase.
"""
