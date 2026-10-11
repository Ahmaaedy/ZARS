"""Phase 2A: Basic Skill Generation (4 tests, sequential)"""
import sys
import json
import time
import subprocess
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.skillgen import generate_skill, save_skill
from core.config import Config

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
    required = ["id", "desc", "handler"]
    missing = [f for f in required if f not in manifest]
    return len(missing) == 0, missing

def validate_code(code):
    required = ["emit", "build_parser", "manifest_for"]
    missing = [r for r in required if r not in code]
    return len(missing) == 0, missing

def test_execution(skill_dir):
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

# ========== Tests ==========
print("\n=== Phase 2A: Basic Skill Generation ===")
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

    # Skip execution for recycle bin test
    if skill_id == "test_recycle":
        print(f"    [SKIP] execution test (requires recycle bin items)")
        continue

    ok, result = test_execution(skills_dir / skill_id)
    test(f"  skill executes", ok, f"error: {result}" if not ok else f"result: {result}")

# ========== Cleanup ==========
print("\n=== Cleanup ===")
for skill_id in generated_skills:
    skill_dir = skills_dir / skill_id
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
        print(f"  Removed: {skill_dir}")

print(f"\n{'='*50}")
print(f"Phase 2A Results: {passed} passed, {failed} failed")
if errors:
    print("\nFailed tests:")
    for name, detail in errors:
        print(f"  - {name}: {detail}")
print(f"{'='*50}")
