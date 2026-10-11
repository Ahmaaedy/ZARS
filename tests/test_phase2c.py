"""Phase 2C: Complex Chain Sequences"""
import sys
import json
import time
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

print("\n=== Phase 2C: Complex Chain Sequences ===")
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

    test(f"  generation time < 120s", elapsed < 120, f"took {elapsed:.1f}s")

    path = save_skill(manifest["id"], manifest, code)
    generated_skills.append(manifest["id"])
    test(f"  skill saved", Path(path).exists())

# Cleanup
print("\n=== Cleanup ===")
for skill_id in generated_skills:
    skill_dir = skills_dir / skill_id
    if skill_dir.exists():
        import shutil
        shutil.rmtree(skill_dir)
        print(f"  Removed: {skill_dir}")

print(f"\n{'='*50}")
print(f"Phase 2C Results: {passed} passed, {failed} failed")
if errors:
    print("\nFailed tests:")
    for name, detail in errors:
        print(f"  - {name}: {detail}")
print(f"{'='*50}")
