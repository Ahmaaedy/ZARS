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
        assert recent[0]["content"] == "hello the
