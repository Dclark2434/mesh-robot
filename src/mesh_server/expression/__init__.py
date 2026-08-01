"""Turning the model's words into speech, gestures, and remembered facts.

Only the pure tag-parsing types are re-exported here. The pipeline processors
live in :mod:`mesh_server.expression.processors` and are imported directly,
so that parsing logic stays testable without the whole real-time stack
installed.
"""

from mesh_server.expression.tags import Tag, TagKind, TagStreamParser

__all__ = ["Tag", "TagKind", "TagStreamParser"]
