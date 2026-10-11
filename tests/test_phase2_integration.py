"""Phase 2: Integration Tests for create_skill pipeline"""
import sys
import json
import time
import subprocess
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.skillgen import generate_skill, save_skill
from core.config import Config

# ========== Setup ==========
config = Config()
root = Path(__file__).resolve().parent.parent
skills_dir = root / "skills"

passed = 0
failed = 0
errors = []
generated_skills = []

def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        errors.append((name, detail))
        print(f"  [FAIL] {name} -- {detail}")

def validate_manifest(manifest):
    """Validate manifest has required fields."""
    required = ["id", "desc", "handler"]
    missing = [f for f in required if f not in manifest]
    return len(missing) == 0, missing

def validate_code(code):
    """Validate generated code has required elements."""
    required = ["emit", "build_parser", "manifest_for"]
    missing = [r for r in required if r not in code]
    return len(missing) == 0, missing

def test_execution(skill_dir):
    """Test that generated skill executes without errors."""
    skill_py = skill_dir / "skill.py"
    if not skill_py.exists():
        return False, "skill.py not found"
    try:
        result = subprocess.run(
            ["python", str(skill_py)],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return False, f"exit code {result.returncode}: {result.stderr[:200]}"
        output = json.loads(result.stdout)
        if not output.get("ok"):
            return False, f"skill returned error: {output.get('error')}"
        return True, output.get("data")
    except subprocess.TimeoutExpired:
        return False, "execution timed out"
    except json.JSONDecodeError:
        return False, f"invalid JSON output: {result.stdout[:200]}"
    except Exception as e:
        return False, str(e)

# ========== 2A: Basic Skill Generation ==========
print("\n=== 2A: Basic Skill Generation ===")
basic_tests = [
    ("opens notepad", "test_notepad"),
    ("check disk space", "test_disk_space"),
    ("show running processes", "test_processes"),
    ("empty recycle bin", "test_recycle"),
]

for desc, skill_id in basic_tests:
    print(f"\n  Testing: '{desc}'")
    start = time.time()
    manifest, code = generate_skill(config, desc, skill_id)
    elapsed = time.time() - start

    # Test generation succeeded
    test(f"  generation succeeded", manifest is not None, f"error: {code}")
    if manifest is None:
        continue

    # Test manifest validity
    valid, missing = validate_manifest(manifest)
    test(f"  manifest valid", valid, f"missing: {missing}")

    # Test code validity
    valid, missing = validate_code(code)
    test(f"  code valid", valid, f"missing: {missing}")

    # Test timing
    test(f"  generation time < 60s", elapsed < 60, f"took {elapsed:.1f}s")

    # Save skill
    path = save_skill(manifest["id"], manifest, code)
    generated_skills.append(manifest["id"])
    test(f"  skill saved", Path(path).exists())

    # Test execution (skip for skills that need specific conditions)
    if skill_id in ["test_recycle"]:
        print(f"    [SKIP] execution test (requires recycle bin items)")
        continue

    ok, result = test_execution(skills_dir / skill_id)
    test(f"  skill executes", ok, f"error: {result}" if not ok else f"result: {result}")

# ========== 2B: Subtle Skill Requests ==========
print("\n=== 2B: Subtle Skill Requests (direct skillgen) ===")
subtle_tests = [
    ("i want to make a skill that opens notepad", "test_subtle_notepad"),
    ("i need a skill to check disk space", "test_subtle_disk"),
    ("help me build a skill that shows weather", "test_subtle_weather"),
    ("make me something that can open apps by name", "test_subtle_launcher"),
]

for desc, skill_id in subtle_tests:
    print(f"\n  Testing: '{desc}'")
    start = time.time()
    manifest, code = generate_skill(config, desc, skill_id)
    elapsed = time.time() - start

    test(f"  generation succeeded", manifest is not None, f"error: {code}")
    if manifest is None:
        continue

    valid, missing = validate_manifest(manifest)
    test(f"  manifest valid", valid, f"missing: {missing}")

    valid, missing = validate_code(code)
    test(f"  code valid", valid, f"missing: {missing}")

    test(f"  generation time < 60s", elapsed < 60, f"took {elapsed:.1f}s")

    path = save_skill(manifest["id"], manifest, code)
    generated_skills.append(manifest["id"])
    test(f"  skill saved", Path(path).exists())

# ========== 2C: Complex Chain Sequences ==========
print("\n=== 2C: Complex Chain Sequences ===")
complex_tests = [
    ("create a skill that first checks disk space then cleans temp files if low", "test_complex_cleanup"),
    ("make a skill that takes a screenshot and saves it to a file", "test_complex_screenshot"),
    ("create a skill that lists all installed programs and exports them to a CSV", "test_complex_export"),
]

for desc, skill_id in complex_tests:
    print(f"\n  Testing: '{desc}'")
    start = time.time()
    manifest, code = generate_skill(config, desc, skill_id)
    elapsed = time.time() - start

    test(f"  generation succeeded", manifest is not None, f"error: {code}")
    if manifest is None:
        continue

    valid, missing = validate_manifest(manifest)
    test(f"  manifest valid", valid, f"missing: {missing}")

    valid, missing = validate_code(code)
    test(f"  code valid", valid, f"missing: {missing}")

    test(f"  generation time < 90s", elapsed < 90, f"took {elapsed:.1f}s")

    path = save_skill(manifest["id"], manifest, code)
    generated_skills.append(manifest["id"])
    test(f"  skill saved", Path(path).exists())

# ========== Cleanup ==========
print("\n=== Cleanup ===")
for skill_id in generated_skills:
    skill_dir = skills_dir / skill_id
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
        print(f"  Removed: {skill_dir}")

# ========== Summary ==========
print(f"\n{'='*50}")
print(f"Phase 2 Results: {passed} passed, {failed} failed")
if errors:
    print("\nFailed tests:")
    for name, detail in errors:
        print(f"  - {name}: {detail}")
print(f"{'='*50}")
