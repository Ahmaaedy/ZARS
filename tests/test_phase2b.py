"""Phase 2B: Subtle Skill Requests"""
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

print("\n=== Phase 2B: Subtle Skill Requests ===")
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
print(f"Phase 2B Results: {passed} passed, {failed} failed")
if errors:
    print("\nFailed tests:")
    for name, detail in errors:
        print(f"  - {name}: {detail}")
print(f"{'='*50}")
