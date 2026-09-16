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
        assert mem2.count() == 0, "cleared memory should be empty on reload"
        print("PASS: clear")
    finally:
        cleanup(tmpdir)


def test_format_for_prompt():
    tmpdir = Path(tempfile.mkdtemp())
    try:
        config = make_config(tmpdir)
        mem = ConversationMemory(config)
        # Empty memory should return empty string
        assert mem.format_for_prompt() == ""
        mem.add("user", "what time is it")
        mem.add("assistant", "it's 3pm")
        result = mem.format_for_prompt()
        assert "Previous conversation context:" in result
        assert "User: what time is it" in result
        assert "ZARS: it's 3pm" in result
        print("PASS: format_for_prompt")
    finally:
        cleanup(tmpdir)


def test_corrupted_json_recovery():
    tmpdir = Path(tempfile.mkdtemp())
    try:
        config = make_config(tmpdir)
        mem_dir = Path(config.memory_dir)
        mem_dir.mkdir(parents=True, exist_ok=True)
        # Write corrupted JSON
        (mem_dir / "recent.json").write_text("NOT VALID JSON {{{", encoding="utf-8")
        # Should not crash
        mem = ConversationMemory(config)
        assert mem.count() == 0, "corrupted JSON should result in empty memory"
        print("PASS: corrupted JSON recovery")
    finally:
        cleanup(tmpdir)


def test_atomic_write():
    tmpdir = Path(tempfile.mkdtemp())
    try:
        config = make_config(tmpdir)
        mem = ConversationMemory(config)
        mem.add("user", "test atomic")
        # .tmp file should not exist after successful write
        tmp_file = Path(config.memory_dir) / "recent.tmp"
        assert not tmp_file.exists(), ".tmp file should be cleaned up after atomic write"
        # main file should exist
        assert (Path(config.memory_dir) / "recent.json").exists()
        print("PASS: atomic write cleanup")
    finally:
        cleanup(tmpdir)


# ========== LONG-TERM MEMORY ==========

def test_save_conversation_turn():
    tmpdir = Path(tempfile.mkdtemp())
    try:
        config = make_config(tmpdir)
        ltm = LongTermMemory(config)
        path = ltm.save_conversation_turn("what's 2+2?", "4")
        assert path.exists(), f"file should exist at {path}"
        content = path.read_text(encoding="utf-8")
        assert "## User" in content
        assert "what's 2+2?" in content
        assert "## ZARS" in content
        assert "4" in content
        assert "type: conversation" in content
        print("PASS: save_conversation_turn")
    finally:
        cleanup(tmpdir)


def test_save_fact():
    tmpdir = Path(tempfile.mkdtemp())
    try:
        config = make_config(tmpdir)
        ltm = LongTermMemory(config)
        path = ltm.save_fact("preference", "User prefers dark mode", tags=["ui", "prefs"])
        content = path.read_text(encoding="utf-8")
        assert "type: fact" in content
        assert "category: preference" in content
        assert "User prefers dark mode" in content
        assert "tags: [ui, prefs]" in content
        print("PASS: save_fact")
    finally:
        cleanup(tmpdir)


def test_search():
    tmpdir = Path(tempfile.mkdtemp())
    try:
        config = make_config(tmpdir)
        ltm = LongTermMemory(config)
        ltm.save_conversation_turn("weather in london", "It's raining")
        ltm.save_conversation_turn("open notepad", "Done")
        results = ltm.search("london")
        assert len(results) == 1
        assert "london" in results[0].lower()
        # Search for something that exists in both
        results = ltm.search("weather")
        assert len(results) == 1
        print("PASS: search")
    finally:
        cleanup(tmpdir)


def test_get_recent_conversations():
    tmpdir = Path(tempfile.mkdtemp())
    try:
        config = make_config(tmpdir)
        ltm = LongTermMemory(config)
        ltm.save_conversation_turn("first question", "first answer")
        ltm.save_conversation_turn("second question", "second answer")
        recent = ltm.get_recent_conversations(5)
        assert len(recent) == 2
        # Most recent first
        assert "second question" in recent[0]
        assert "first question" in recent[1]
        print("PASS: get_recent_conversations")
    finally:
        cleanup(tmpdir)


def test_conversation_file_is_single_file_per_turn():
    tmpdir = Path(tempfile.mkdtemp())
    try:
        config = make_config(tmpdir)
        ltm = LongTermMemory(config)
        path = ltm.save_conversation_turn("hello", "hey there")
        # One file should contain BOTH user and assistant
        content = path.read_text(encoding="utf-8")
        assert "## User" in content
        assert "## ZARS" in content
        assert "hello" in content
        assert "hey there" in content
        # Should be a single file, not split
        files = list((Path(config.memory_dir) / "conversations").glob("*.md"))
        assert len(files) == 1, f"expected 1 file for one turn, got {len(files)}"
        print("PASS: single file per conversation turn")
    finally:
        cleanup(tmpdir)


def test_count():
    tmpdir = Path(tempfile.mkdtemp())
    try:
        config = make_config(tmpdir)
        ltm = LongTermMemory(config)
        assert ltm.count() == 0
        ltm.save_conversation_turn("q1", "a1")
        ltm.save_conversation_turn("q2", "a2")
        ltm.save_fact("test", "fact content")
        assert ltm.count() == 3
        print("PASS: count")
    finally:
        cleanup(tmpdir)


# ========== SESSION SEEDING ==========

def test_session_seed():
    session = Session("You are ZARS.")
    session.add("user", "current question")
    msgs = [
        {"role": "user", "content": "old question 1"},
        {"role": "assistant", "content": "old answer 1"},
        {"role": "user", "content": "old question 2"},
        {"role": "assistant", "content": "old answer 2"},
    ]
    session.seed(msgs)
    # System prompt should still be first
    assert session.messages[0]["role"] == "system"
    assert session.messages[0]["content"] == "You are ZARS."
    # Seeded messages come next
    assert session.messages[1]["content"] == "old question 1"
    assert session.messages[2]["content"] == "old answer 1"
    assert session.messages[3]["content"] == "old question 2"
    assert session.messages[4]["content"] == "old answer 2"
    # Current user message is last
    assert session.messages[5]["content"] == "current question"
    assert len(session.messages) == 6
    print("PASS: session seed ordering")


def test_session_seed_empty():
    session = Session("You are ZARS.")
    session.add("user", "question")
    session.seed([])
    # Should not change anything
    assert len(session.messages) == 2
    assert session.messages[1]["content"] == "question"
    print("PASS: session seed empty")


def test_session_tool_result():
    session = Session("system prompt")
    session.add("user", "question")
