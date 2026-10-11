"""Phase 3: Edge Case Tests"""
import sys
import json
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.skillgen import generate_skill, save_skill, _extract_json, _extract_code, _validate_manifest
from core.config import Config
from core.safety import check_blocked
from core.fuzzy_match import fuzzy_match, normalize
from core.registry import load_registry
from core.macros import MacroManager

config = Config()
root = Path(__file__).resolve().parent.parent
skills_dir = root / "skills"

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

# ========== 3A: JSON Extraction Edge Cases ==========
print("\n=== 3A: JSON Extraction Edge Cases ===")

# Test: clean JSON
result = _extract_json('{"id": "test", "desc": "test"}')
test("clean JSON parsed", result is not None and result.get("id") == "test")

# Test: JSON wrapped in markdown code block
result = _extract_json('```json\n{"id": "test", "desc": "test"}\n```')
test("markdown JSON parsed", result is not None and result.get("id") == "test")

# Test: JSON with surrounding text
result = _extract_json('Here is the JSON:\n{"id": "test", "desc": "test"}\nDone.')
test("JSON with surrounding text parsed", result is not None and result.get("id") == "test")

# Test: invalid JSON
result = _extract_json("this is not json at all")
test("invalid JSON returns None", result is None)

# Test: empty string
result = _extract_json("")
test("empty string returns None", result is None)

# Test: nested JSON
result = _extract_json('{"id": "test", "params": [{"name": "x"}]}')
test("nested JSON parsed", result is not None and len(result.get("params", [])) == 1)

# ========== 3B: Code Extraction Edge Cases ==========
print("\n=== 3B: Code Extraction Edge Cases ===")

# Test: clean code
result = _extract_code("import sys\nprint('hello')")
test("clean code extracted", "import sys" in result)

# Test: code in markdown block
result = _extract_code("```python\nimport sys\nprint('hello')\n```")
test("markdown code extracted", "import sys" in result)

# Test: empty code
result = _extract_code("")
test("empty code returns empty", result == "")

# ========== 3C: Manifest Validation Edge Cases ==========
print("\n=== 3C: Manifest Validation Edge Cases ===")

# Test: valid manifest
manifest = {"id": "test", "desc": "test", "handler": {"type": "skill"}}
errors_list = _validate_manifest(manifest)
test("valid manifest passes", len(errors_list) == 0)

# Test: missing id
manifest = {"desc": "test", "handler": {"type": "skill"}}
errors_list = _validate_manifest(manifest)
test("missing id detected", any("id" in e for e in errors_list))

# Test: missing desc
manifest = {"id": "test", "handler": {"type": "skill"}}
errors_list = _validate_manifest(manifest)
test("missing desc detected", any("desc" in e for e in errors_list))

# Test: bad param type
manifest = {"id": "test", "desc": "test", "params": [{"name": "x", "type": "invalid"}]}
errors_list = _validate_manifest(manifest)
test("bad param type detected", any("type" in e for e in errors_list))

# Test: bad risk
manifest = {"id": "test", "desc": "test", "risk": "invalid"}
errors_list = _validate_manifest(manifest)
test("bad risk detected", any("risk" in e for e in errors_list))

# ========== 3D: Safety Edge Cases ==========
print("\n=== 3D: Safety Edge Cases ===")

# Test: empty command
blocked, reason = check_blocked("")
test("empty command not blocked", not blocked)

# Test: safe command
blocked, reason = check_blocked("Get-Date")
test("Get-Date not blocked", not blocked)

# Test: case insensitivity
blocked, reason = check_blocked("DISKPART")
test("DISKPART blocked (case insensitive)", blocked)

# Test: partial match not blocked
blocked, reason = check_blocked("diskpart_utility")
test("diskpart_utility not blocked (partial)", not blocked)

# Test: word boundary
blocked, reason = check_blocked("mydiskpart")
test("mydiskpart not blocked (no word boundary)", not blocked)

# ========== 3E: Fuzzy Match Edge Cases ==========
print("\n=== 3E: Fuzzy Match Edge Cases ===")
registry = load_registry()
macros = MacroManager(root / "macros.json")

# Test: empty input
result = fuzzy_match("", registry, macros)
test("empty input returns None", result is None)

# Test: whitespace only
result = fuzzy_match("   ", registry, macros)
test("whitespace returns None", result is None)

# Test: very long input
result = fuzzy_match("open " * 50 + "notepad", registry, macros)
test("long input handles gracefully", result is not None or result is None)  # No crash

# Test: special characters
result = fuzzy_match("open@#$%^&*()notepad", registry, macros)
test("special chars handle gracefully", True)  # No crash

# Test: unicode
result = fuzzy_match("open \u97f3\u4e50\u64ad\u653e\u5668", registry, macros)
test("unicode handles gracefully", True)  # No crash

# ========== 3F: Normalization Edge Cases ==========
print("\n=== 3F: Normalization Edge Cases ===")

test("normalize empty string", normalize("") == "")
test("normalize whitespace", normalize("  hello  ") == "hello")
test("normalize special chars", normalize("hello-world!") == "hello world")
test("normalize uppercase", normalize("HELLO") == "hello")
test("normalize multiple spaces", normalize("hello   world") == "hello world")

# ========== Summary ==========
print(f"\n{'='*50}")
print(f"Phase 3 Results: {passed} passed, {failed} failed")
if errors:
    print("\nFailed tests:")
    for name, detail in errors:
        print(f"  - {name}: {detail}")
print(f"{'='*50}")
