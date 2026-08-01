"""Long-term memory behind the ``[MEMORY: ...]`` tag.

Previously this tag was parsed, stripped, and discarded -- the prompt spent
tokens teaching the model a capability that did nothing. This gives it a
backing store.

The design is deliberately small. Facts are short strings the model chose to
remember, kept in one JSON file, deduplicated, and injected into the system
prompt at startup. Within a session the model already has the fact in its
context because it just said it; the store exists so the robot still knows
your name tomorrow.

The legacy ``mesh_memory.json`` transcript dump from the pre-streaming
architecture is left untouched -- it holds raw conversation logs, not facts.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from mesh_common.logging import get_logger

logger = get_logger("memory")

#: Beyond this, the prompt cost stops being worth it and old facts are evicted.
MAX_FACTS = 120

#: Facts longer than this are almost always the model narrating rather than
#: recording something durable.
MAX_FACT_CHARS = 200


@dataclass(frozen=True)
class Fact:
    """One remembered statement.

    Attributes:
        text: The fact as the model phrased it.
        recorded_at: Unix timestamp of when it was first stored.
    """

    text: str
    recorded_at: float


def _normalize(text: str) -> str:
    """Reduce a fact to a comparison key.

    Args:
        text: Raw fact text.

    Returns:
        Lowercased text with punctuation and repeated whitespace removed, so
        "User's name is Dustin." and "user's name is dustin" collapse together.
    """
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


class MemoryStore:
    """A deduplicated, size-capped set of remembered facts on disk."""

    def __init__(self, path: Path) -> None:
        """Load the store, tolerating a missing or corrupt file.

        Args:
            path: JSON file backing the store. Created on first write.
        """
        self._path = path
        self._facts: list[Fact] = []
        self._keys: set[str] = set()
        self._load()

    def _load(self) -> None:
        """Read facts from disk, ignoring anything unparseable."""
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for entry in raw.get("facts", []):
                self._insert(Fact(entry["text"], float(entry.get("recorded_at", 0))))
            logger.info(f"Recalled {len(self._facts)} facts from {self._path.name}")
        except (ValueError, KeyError, TypeError, OSError) as exc:
            logger.warning(f"Could not read memory ({exc}); starting empty")

    def _insert(self, fact: Fact) -> bool:
        """Add a fact if it is new, evicting the oldest when full.

        Args:
            fact: The fact to store.

        Returns:
            True if it was stored, False if it duplicated an existing fact.
        """
        key = _normalize(fact.text)
        if not key or key in self._keys:
            return False
        self._facts.append(fact)
        self._keys.add(key)
        while len(self._facts) > MAX_FACTS:
            self._keys.discard(_normalize(self._facts.pop(0).text))
        return True

    def remember(self, text: str) -> bool:
        """Record a fact and persist it immediately.

        Args:
            text: Fact text as written by the model.

        Returns:
            True if the fact was new and has been written to disk.
        """
        text = text.strip()
        if not text or len(text) > MAX_FACT_CHARS:
            return False
        if not self._insert(Fact(text, time.time())):
            return False
        self._save()
        logger.info(f"Remembered: {text}")
        return True

    def _save(self) -> None:
        """Write the store to disk atomically."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "facts": [{"text": f.text, "recorded_at": f.recorded_at} for f in self._facts]
            }
            temp = self._path.with_suffix(".tmp")
            temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            temp.replace(self._path)
        except OSError as exc:
            logger.error(f"Could not save memory: {exc}")

    def as_prompt_block(self) -> str:
        """Render remembered facts for injection into the system prompt.

        Returns:
            A prompt section, or an empty string when nothing is remembered
            (so a fresh robot isn't told it has an empty memory).
        """
        if not self._facts:
            return ""
        lines = "\n".join(f"- {fact.text}" for fact in self._facts)
        return (
            "========================\n"
            "WHAT YOU REMEMBER\n"
            "========================\n"
            "Things you learned in earlier conversations. Use them naturally;\n"
            "do not recite them back unprompted.\n"
            f"{lines}"
        )

    def __len__(self) -> int:
        """Number of facts currently stored."""
        return len(self._facts)
