"""Tests for the long-term memory store behind the [MEMORY: ...] tag."""

from mesh_server.memory import MAX_FACTS, MemoryStore


def test_facts_survive_a_restart(tmp_path):
    path = tmp_path / "facts.json"
    MemoryStore(path).remember("Dustin is building a hexapod")
    assert "hexapod" in MemoryStore(path).as_prompt_block()


def test_duplicates_are_not_stored_twice(tmp_path):
    store = MemoryStore(tmp_path / "facts.json")
    assert store.remember("User is called Dustin")
    # Same fact, different punctuation and case; the model rephrases.
    assert not store.remember("user is called dustin.")
    assert len(store) == 1


def test_blank_and_overlong_facts_are_refused(tmp_path):
    store = MemoryStore(tmp_path / "facts.json")
    assert not store.remember("   ")
    assert not store.remember("x" * 500)
    assert len(store) == 0


def test_oldest_facts_are_evicted_when_full(tmp_path):
    store = MemoryStore(tmp_path / "facts.json")
    for i in range(MAX_FACTS + 10):
        store.remember(f"fact number {i}")
    assert len(store) == MAX_FACTS
    block = store.as_prompt_block()
    assert "fact number 0" not in block
    assert f"fact number {MAX_FACTS + 9}" in block


def test_empty_store_contributes_nothing_to_the_prompt(tmp_path):
    # A fresh robot should not be told that it remembers nothing.
    assert MemoryStore(tmp_path / "facts.json").as_prompt_block() == ""


def test_corrupt_file_does_not_prevent_startup(tmp_path):
    path = tmp_path / "facts.json"
    path.write_text("{ this is not json", encoding="utf-8")
    store = MemoryStore(path)
    assert len(store) == 0
    assert store.remember("still works")
