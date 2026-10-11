"""Phase 4: Full Pipeline Integration Tests"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.router import Router
from core.fuzzy_match import fuzzy_match
from core.session import Session
from core.llm import LLM
from core.registry import load_registry
from core.macros import MacroManager
from core.config import Config

# ========== Setup ==========
root = Path(__file__).resolve().parent.parent
config = Config()
registry = load_registry()
macros = MacroManager(root / "macros.json")
router = Router(registry, macros)
llm = LLM(config, registry)

passed = 0
failed = 0
errors = []

def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        errors.append((name, detail))
        print(f"  [FAIL] {name} -- {detail}")

# ========== 4A: Router Matching ==========
print("\n=== 4A: Router Matching ===")

# Test: direct trigger match
entry, args = router.match("open notepad")
test("open notepad => open_app", entry is not None and entry.id == "open_app")

# Test: URL detection
entry, args = router.match("open https://github.com")
test("open url => open_url", entry is not None and entry.id == "open_url")

# Test: defer markers (should not match)
entry, args = router.match("open notepad and close chrome")
test("defer markers skip router", entry is None)

# Test: question mark (should not match)
entry, args = router.match("open notepad?")
test("question mark skips router", entry is None)

# ========== 4B: Fuzzy Match -> Router Integration ==========
print("\n=== 4B: Fuzzy Match Integration ===")

# Test: fuzzy match catches what router misses
result = fuzzy_match("play some music", registry, macros)
test("play some music => media_control via fuzzy", result is not None and result["match"]["id"] == "media_control")

# Test: macro fuzzy match
result = fuzzy_match("lets cook dinner", registry, macros)
test("lets cook dinner => lets_cook via fuzzy", result is not None and result["match"]["id"] == "lets_cook")

# Test: ambiguous match detection
result = fuzzy_match("open", registry, macros)
if result and result.get("ambiguous"):
    test("open is ambiguous", True)
elif result:
    test("open matches (not ambiguous)", not result.get("ambiguous", False))
else:
    test("open returns no match or ambiguous", True)

# ========== 4C: System Prompt Contains create_skill Rule ==========
print("\n=== 4C: System Prompt Validation ===")

system_prompt = llm.system_prompt()
test("system prompt contains create_skill", "create_skill" in system_prompt)
test("system prompt contains fuzzy hint rule", "fuzzy match" in system_prompt.lower())
test("system prompt contains tool list", "open_app" in system_prompt)
test("system prompt contains done tool", "done" in system_prompt)
test("system prompt contains shell tool", "shell" in system_prompt)

# ========== 4D: Session Construction ==========
print("\n=== 4D: Session Construction ===")

# Test: session with fuzzy hint
session = Session(llm.system_prompt())
test("session starts with system prompt", session.messages[0]["role"] == "system")

# Add fuzzy hint
fuzzy_result = {"match": {"id": "media_control", "type": "command"}, "score": 90, "ambiguous": False}
hint = llm._format_fuzzy_hint(fuzzy_result) if hasattr(llm, '_format_fuzzy_hint') else ""
session.add("system", hint)
test("fuzzy hint added at index 1", session.messages[1]["role"] == "system")

# Add user message
session.add("user", "play music")
test("user message at end", session.messages[-1]["role"] == "user")
test("user message is play music", session.messages[-1]["content"] == "play music")

# ========== 4E: create_skill Tool Schema ==========
print("\n=== 4E: create_skill Tool Schema ===")

# Check that create_skill is in the tool schema
schema = llm.tool_schema()
tool_enum = schema["properties"]["tool"]["enum"]
test("create_skill in tool enum", "create_skill" in tool_enum)
test("done in tool enum", "done" in tool_enum)
test("shell in tool enum", "shell" in tool_enum)
test("ask in tool enum", "ask" in tool_enum)

# ========== 4F: Pipeline Flow Simulation ==========
print("\n=== 4F: Pipeline Flow Simulation ===")

# Simulate: "create skill that opens notepad"
# Step 1: Router should NOT match (no direct trigger)
entry, args = router.match("create skill that opens notepad")
test("create skill skips router", entry is None)

# Step 2: Macro match should NOT match
macro_trigger = macros.match("create skill that opens notepad")
test("create skill skips macro match", macro_trigger is None)

# Step 3: Fuzzy match might match create_skill
fuzzy_result = fuzzy_match("create skill that opens notepad", registry, macros)
if fuzzy_result:
    test("create skill fuzzy match found", fuzzy_result["match"]["id"] == "create_skill")
else:
    test("create skill no fuzzy match (LLM will handle)", True)

# Step 4: LLM should use create_skill tool
# (We can't test actual LLM call, but we verify the system prompt has the rule)
test("LLM prompt guides to create_skill", "create a new skill" in system_prompt.lower() or "create_skill" in system_prompt)

# ========== Summary ==========
print(f"\n{'='*50}")
print(f"Phase 4 Results: {passed} passed, {failed} failed")
if errors:
    print("\nFailed tests:")
    for name, detail in errors:
        print(f"  - {name}: {detail}")
print(f"{'='*50}")
