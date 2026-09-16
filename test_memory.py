"""Rigorous tests for the memory system."""
import json
import shutil
import tempfile
from pathlib import Path

from core.memory import ConversationMemory
from core.longterm_memory import LongTermMemory
from core.session import Session
from core.config import Config


def make_config(tmpdir):
    """Create a config pointing at a temp directory."""
    c = Config()
    c.memory_dir = str(tmpdir / "memory")
    c.memory_recent = 5
    return c


def cleanup(path):
    shutil.rmtree(path, ignore_errors=True)


# ========== SHORT-TERM MEMORY ==========

def test_save_load_roundtrip():
    tmpdir = Path(tempfile.mkdtemp())
    try:
        config = make_config(tmpdir)
        mem = ConversationMemory(config)
        assert mem.count() == 0, "fresh memory should be empty"

        mem.add("user", "hello there")
        mem.add("assistant", "hey!")
        assert mem.count() == 2

        # Create a NEW instance to verify disk persistence
        mem2 = ConversationMemory(config)
        assert mem2.count() == 2, "reloaded memory should have 2 messages"
        recent = mem2.get_recent()
        assert recent[0]["role"] == "user"
        assert recent[0]["content"] == "hello there"
        assert recent[1]["role"] == "assistant"
        assert recent[1]["content"] == "hey!"
        print("PASS: save/load roundtrip")
    finally:
        cleanup(tmpdir)


def test_max_messages_trimming():
    tmpdir = Path(tempfile.mkdtemp())
    try:
        config = make_config(tmpdir)
        mem = ConversationMemory(config)
        # memory_recent = 5, so we can have at most 5 messages
        # Add 10 messages (5 pairs)
        for i in range(5):
            mem.add("user", f"msg {i}")
            mem.add("assistant", f"reply {i}")
        # Should be trimmed to 5
        assert mem.count() == 5, f"expected 5 after trimming, got {mem.count()}"
        # The oldest surviving should be reply 2 (msg 0,1,2 trimmed)
        recent = mem.get_recent()
        assert recent[0]["content"] == "reply 2", f"oldest should be 'reply 2', got '{recent[0]['content']}'"
        assert recent[-1]["content"] == "reply 4"
        print("PASS: max messages trimming")
    finally:
        cleanup(tmpdir)


def test_clear():
    tmpdir = Path(tempfile.mkdtemp())
    try:
        config = make_config(tmpdir)
        mem = ConversationMemory(config)
        mem.add("user", "test")
        mem.add("assistant", "response")
        assert mem.count() == 2
        mem.clear()
        assert mem.count() == 0
        # Verify persistence after clear
        mem2 = ConversationMemory(config)
        assert mem2.count() == 0, "cleared memory should be empty o
