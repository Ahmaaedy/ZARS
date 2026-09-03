import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.registry import load_registry, Registry, RegistryError, Entry
from core.router import Router, _tokenize


def _build_test_registry():
    entries = {}
    raws = [
        {
            "id": "open_app", "desc": "open app", "tier": "core",
            "triggers": ["open", "launch", "start app"],
            "params": [{"name": "app_name", "type": "string", "required": True, "desc": "app"}],
            "keywords": ["launch"], "examples": [], "risk": "safe",
            "handler": {"type": "builtin", "fn": "noop"},
        },
        {
            "id": "open_url", "desc": "open url", "tier": "core",
            "triggers": ["open", "go to"],
            "params": [{"name": "url", "type": "string", "required": True, "desc": "url"}],
            "keywords": ["go to"], "examples": [], "risk": "safe",
            "handler": {"type": "builtin", "fn": "noop"},
        },
        {
            "id": "close_app", "desc": "close app", "tier": "core",
            "triggers": ["close", "quit app"],
            "params": [{"name": "app_name", "type": "string", "required": True, "desc": "app"}],
            "keywords": ["close"], "examples": [], "risk": "confirm",
            "handler": {"type": "builtin", "fn": "noop"},
        },
        {
            "id": "set_volume", "desc": "set volume", "tier": "core",
            "triggers": ["set volume", "volume to"],
            "params": [{"name": "level", "type": "integer", "required": True, "desc": "level"}],
            "keywords": ["set volume"], "examples": [], "risk": "safe",
            "handler": {"type": "builtin", "fn": "noop"},
        },
        {
            "id": "toggle_wifi", "desc": "toggle wifi", "tier": "core",
            "triggers": ["wifi", "toggle wifi"],
            "params": [{"name": "state", "type": "string", "required": True, "desc": "on/off"}],
            "keywords": ["wifi on"], "examples": [], "risk": "confirm",
            "handler": {"type": "builtin", "fn": "noop"},
        },
        {
            "id": "set_timer", "desc": "set timer", "tier": "core",
            "triggers": ["set timer", "start timer", "timer for"],
            "params": [{"name": "duration", "type": "string", "required": True, "desc": "duration"}],
            "keywords": ["set timer"], "examples": [], "risk": "safe",
            "handler": {"type": "builtin", "fn": "noop"},
        },
        {
            "id": "write_note", "desc": "write note", "tier": "core",
            "triggers": ["note down", "write note", "remember"],
            "params": [{"name": "content", "type": "string", "required": True, "desc": "content"}],
            "keywords": ["write note"], "examples": [], "risk": "safe",
            "handler": {"type": "builtin", "fn": "noop"},
        },
        {
            "id": "search_notes", "desc": "search notes", "tier": "core",
            "triggers": ["search notes", "find note"],
            "params": [{"name": "query", "type": "string", "required": True, "desc": "query"}],
            "keywords": ["search notes"], "examples": [], "risk": "safe",
            "handler": {"type": "builtin", "fn": "noop"},
        },
        {
            "id": "web_search", "desc": "web search", "tier": "skill",
            "triggers": ["search", "google", "look up"],
            "params": [{"name": "query", "type": "string", "required": True, "desc": "query"}],
            "keywords": ["search"], "examples": [], "risk": "safe",
            "handler": {"type": "skill", "run": "skills/web_search/skill.py"},
        },
        {
            "id": "lets_cook", "desc": "setup workspace", "tier": "skill",
            "triggers": ["lets cook", "setup workspace"],
            "params": [],
            "keywords": [], "examples": [], "risk": "safe",
            "handler": {"type": "skill", "run": "skills/lets cook/skill.py"},
        },
    ]
    for raw in raws:
        entry = Entry(
            id=raw["id"], desc=raw["desc"], params=raw["params"],
            keywords=raw["keywords"], triggers=raw["triggers"], tier=raw["tier"],
            examples=raw["examples"], handler=raw["handler"], risk=raw["risk"],
            source="test",
        )
        entries[entry.id] = entry
    return Registry(entries, [])


TEST_CASES = [
    # --- Tier 0: core commands ---
    ("open notepad", "open_app", {"app_name": "notepad"}),
    ("open chrome", "open_app", {"app_name": "chrome"}),
    ("launch notepad", "open_app", {"app_name": "notepad"}),
    ("launch chrome", "open_app", {"app_name": "chrome"}),
    ("start app calc", "open_app", {"app_name": "calc"}),

    ("open https://github.com", "open_url", {"url": "https://github.com"}),
    ("open http://example.com", "open_url", {"url": "http://example.com"}),
    ("go to youtube.com", "open_url", {"url": "youtube.com"}),
    ("go to https://google.com", "open_url", {"url": "https://google.com"}),

    ("close notepad", "close_app", {"app_name": "notepad"}),
    ("quit app chrome", "close_app", {"app_name": "chrome"}),

    ("set volume 50", "set_volume", {"level": "50"}),
    ("volume to 80", "set_volume", {"level": "80"}),

    ("wifi on", "toggle_wifi", {"state": "on"}),
    ("wifi off", "toggle_wifi", {"state": "off"}),
    ("toggle wifi", None, None),  # no arg -> falls through

    ("set timer 30s", "set_timer", {"duration": "30s"}),
    ("start timer 5m", "set_timer", {"duration": "5m"}),
    ("timer for 1h30m", "set_timer", {"duration": "1h30m"}),

    ("note down buy milk", "write_note", {"content": "buy milk"}),
    ("write note meeting at 3pm", "write_note", {"content": "meeting at 3pm"}),
    ("remember to call mom", "write_note", {"content": "to call mom"}),

    ("search notes milk", "search_notes", {"query": "milk"}),
    ("find note meeting", "search_notes", {"query": "meeting"}),

    # --- Tier 1 fallback: skills and unknown ---
    ("lets cook", None, None),
    ("setup workspace", None, None),
    ("search for chocolate", None, None),
    ("weather in london", None, None),

    # --- Compound -> LLM ---
    ("open notepad and take a screenshot", None, None),
    ("set volume 30 then open chrome", None, None),

    # --- Edge cases ---
    ("", None, None),
    ("   ", None, None),
    ("open", None, None),  # bare verb, no arg -> falls through
    ("close", None, None),  # bare verb, no arg -> falls through
]


def test_tokenize():
    assert _tokenize("") == []
    assert _tokenize("hello") == [("hello", 0, 5)]
    assert _tokenize("open notepad") == [("open", 0, 4), ("notepad", 5, 12)]
    assert _tokenize("open https://github.com") == [
        ("open", 0, 4), ("https://github.com", 5, 23)
    ]
    assert _tokenize("go to youtube.com") == [
        ("go", 0, 2), ("to", 3, 5), ("youtube.com", 6, 17)
    ]


def test_routing():
    registry = _build_test_registry()
    router = Router(registry)
    passed = 0
    failed = 0

    for text, expected_id, expected_args in TEST_CASES:
        entry, args = router.match(text)
        actual_id = entry.id if entry else None
        actual_args = args if args else None

        if actual_id == expected_id and actual_args == expected_args:
            passed += 1
        else:
            failed += 1
            print(f"  FAIL: {text!r}")
            print(f"    expected: id={expected_id}, args={expected_args}")
            print(f"    actual:   id={actual_id}, args={actual_args}")

    return passed, failed


def test_skill_cannot_claim_reserved_verb():
    from core.registry import RESERVED_VERBS
    entries = {
        "bad": Entry(
            id="bad", desc="bad skill", params=[], keywords=[],
            triggers=["open"], tier="skill", examples=[],
            handler={"type": "skill", "run": "x"}, risk="safe", source="test",
        ),
    }
    try:
        from core.registry import _lint_triggers
        _lint_triggers(entries)
        print("  FAIL: expected RegistryError for skill claiming 'open'")
        return False
    except RegistryError:
        return True


def test_duplicate_same_param_rejected():
    entries = {
        "a": Entry(
            id="a", desc="a", params=[{"name": "app_name", "type": "string", "required": True, "desc": "x"}],
            keywords=[], triggers=["open"], tier="core", examples=[],
            handler={"type": "builtin", "fn": "x"}, risk="safe", source="test",
        ),
        "b": Entry(
            id="b", desc="b", params=[{"name": "app_name", "type": "string", "required": True, "desc": "x"}],
            keywords=[], triggers=["open"], tier="core", examples=[],
            handler={"type": "builtin", "fn": "y"}, risk="safe", source="test",
        ),
    }
    try:
        from core.registry import _lint_triggers
        _lint_triggers(entries)
        print("  FAIL: expected RegistryError for duplicate trigger with same param")
        return False
    except RegistryError:
        return True


def test_duplicate_different_param_allowed():
    entries = {
        "a": Entry(
            id="a", desc="a", params=[{"name": "app_name", "type": "string", "required": True, "desc": "x"}],
            keywords=[], triggers=["open"], tier="core", examples=[],
            handler={"type": "builtin", "fn": "x"}, risk="safe", source="test",
        ),
        "b": Entry(
            id="b", desc="b", params=[{"name": "url", "type": "string", "required": True, "desc": "x"}],
            keywords=[], triggers=["open"], tier="core", examples=[],
            handler={"type": "builtin", "fn": "y"}, risk="safe", source="test",
        ),
    }
    try:
        from core.registry import _lint_triggers
        _lint_triggers(entries)
        return True
    except RegistryError:
        print("  FAIL: should allow duplicate trigger with different param")
        return False


if __name__ == "__main__":
    print("=== tokenize ===")
    test_tokenize()
    print("  tokenize: OK")

    print("\n=== routing ===")
    passed, failed = test_routing()
    print(f"  {passed} passed, {failed} failed")

    print("\n=== linter: skill cannot claim reserved verb ===")
    ok1 = test_skill_cannot_claim_reserved_verb()
    print(f"  {'OK' if ok1 else 'FAIL'}")

    print("\n=== linter: duplicate trigger with same param rejected ===")
    ok2 = test_duplicate_same_param_rejected()
    print(f"  {'OK' if ok2 else 'FAIL'}")

    print("\n=== linter: duplicate trigger with different param allowed ===")
    ok3 = test_duplicate_different_param_allowed()
    print(f"  {'OK' if ok3 else 'FAIL'}")

    total_failed = failed + (0 if ok1 else 1) + (0 if ok2 else 1) + (0 if ok3 else 1)
    if total_failed:
        print(f"\n{total_failed} test(s) FAILED")
        sys.exit(1)
    else:
        print("\nAll tests passed")
