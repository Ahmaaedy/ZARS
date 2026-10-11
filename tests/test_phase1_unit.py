"""Phase 1: Unit Tests for Fuzzy Match, Safety, and Session"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.fuzzy_match import fuzzy_match, normalize
from core.safety import check_blocked
from core.session import Session
from core.registry import load_registry
from core.macros import MacroManager

# ========== Setup ==========
def setup_registry():
    """Load real registry and macros."""
    root = Path(__file__).resolve().parent.parent
    registry = load_registry()
    macros = MacroManager(root / "macros.json")
    return registry, macros

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
        print(f"  [FAIL] {name} — {detail}")

# ========== 1A: Fuzzy Match Tests ==========
print("\n=== 1A: Fuzzy Match Tests ===")
registry, macros = setup_registry()

test_cases = [
    ("play music", "media_control", "command"),
    ("pause", "media_control", "command"),
    ("lets cook", "lets_cook", "macro"),
    ("cleanup all", "cleanup", "macro"),
    ("good morning", "good_morning", "macro"),
    ("play", "media_control", "command"),
    ("toggle wifi on", "toggle_wifi", "command"),
    ("set timer for 5 minutes", "set_timer", "command"),
    ("open chrome", "open_app", "command"),
    ("xyz random gibberish", None, "none"),
]

for input_text, expected_id, expected_type in test_cases:
    result = fuzzy_match(input_text, registry, macros)
    if expected_id is None:
        test(f"'{input_text}' => no match", result is None)
    else:
        if result is None:
            test(f"'{input_text}' => {expected_id}", False, "got None")
        else:
            match = result["match"]
            test(
                f"'{input_text}' => {expected_id}",
                match["id"] == expected_id,
                f"got {match['id']} (score={result['score']})"
            )
            test(
                f"  type is {expected_type}",
                match["type"] == expected_type,
                f"got {match['type']}"
            )

# Test deduplication: macro trigger should NOT match as core command
result = fuzzy_match("cleanup", registry, macros)
if result:
    test(
        "cleanup matches as macro (not command)",
        result["match"]["type"] == "macro",
        f"got type={result['match']['type']}"
    )

# ========== 1B: Safety Blocklist Tests ==========
print("\n=== 1B: Safety Blocklist Tests ===")
safety_tests = [
    ("diskpart", True, "diskpart"),
    ("format C:", True, "format"),
    ("Remove-Item -Recurse C:\\temp", True, "recursive delete"),
    ("shutdown /s /t 0", True, "power action"),
    ("Get-Process", False, "safe command"),
    ("Start-Process notepad", False, "safe command"),
    ("reg delete HKLM\\SOFTWARE\\Test", True, "registry edit"),
    ("schtasks /create /tn Test /tr notepad", True, "scheduled task"),
    ("Get-Date", False, "safe command"),
    ("Stop-Process -Name chrome -Force", False, "safe command"),
]

for command, should_block, reason in safety_tests:
    blocked, block_reason = check_blocked(command)
    test(
        f"'{command}' {'BLOCKED' if should_block else 'ALLOWED'}",
        blocked == should_block,
        f"expected {'blocked' if should_block else 'allowed'}, got {'blocked' if blocked else 'allowed'}: {block_reason}"
    )

# ========== 1C: Session Message Ordering ==========
print("\n=== 1C: Session Message Ordering Tests ===")

# Test 1: System prompt at index 0
session = Session("test system prompt")
test("system prompt at index 0", session.messages[0]["content"] == "test system prompt")
test("only one message initially", len(session.messages) == 1)

# Test 2: System message inserted at index 1
session.add("system", "fuzzy hint")
test("system hint at index 1", session.messages[1]["content"] == "fuzzy hint")
test("system prompt still at index 0", session.messages[0]["role"] == "system")

# Test 3: User message appended at end
session.add("user", "hello")
test("user message at end", session.messages[-1]["content"] == "hello")
test("user message role is user", session.messages[-1]["role"] == "user")

# Test 4: Seed inserts after system prompt
session2 = Session("system prompt")
session2.seed([{"role": "user", "content": "old user"}, {"role": "assistant", "content": "old assistant"}])
test("seeded user at index 1", session2.messages[1]["content"] == "old user")
test("seeded assistant at index 2", session2.messages[2]["content"] == "old assistant")
test("system prompt still at index 0", session2.messages[0]["role"] == "system")

# Test 5: Tool result adds user message
session3 = Session("system")
session3.tool_result("command output here")
test("tool result is user message", session3.messages[-1]["role"] == "user")
test("tool result contains output", "command output here" in session3.messages[-1]["content"])

# ========== Summary ==========
print(f"\n{'='*50}")
print(f"Phase 1 Results: {passed} passed, {failed} failed")
if errors:
    print("\nFailed tests:")
    for name, detail in errors:
        print(f"  - {name}: {detail}")
print(f"{'='*50}")
