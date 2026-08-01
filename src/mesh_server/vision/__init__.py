"""Letting the robot look at things.

Only the pure context-pruning rules are re-exported here. The pipeline
processors and the ``look`` tool live in :mod:`mesh_server.vision.feed` and are
imported directly, so the pruning logic stays testable without the whole
real-time stack installed.
"""

from mesh_server.vision.context import DEFAULT_KEEP_IMAGES, collapse_old_images

__all__ = ["DEFAULT_KEEP_IMAGES", "collapse_old_images"]
