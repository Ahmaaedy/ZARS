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
