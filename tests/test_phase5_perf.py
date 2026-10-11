"""Phase 5: Performance Tests"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.fuzzy_match import fuzzy_match
from core.registry import load_registry
from core.macros import MacroManager

root = Path(__file__).resolve().parent.parent
registry = load_registry()
macros = MacroManager(root / "macros.json")

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

# ========== 5A: Fuzzy Match Performance ==========
print("\n=== 5A: Fuzzy Match Performance ===")

# Warm up
for _ in range(10):
    fuzzy_match("play music", registry, macros)

# Benchmark: 100 calls
test_inputs = [
    "play music",
    "pause",
    "lets cook",
    "cleanup all",
    "good morning",
    "open chrome",
    "toggle wifi on",
    "set timer for 5 minutes",
]

iterations = 100
start = time.time()
for _ in range(iterations):
    for text in test_inputs:
        fuzzy_match(text, registry, macros)
elapsed = time.time() - start
per_call = (elapsed / iterations / len(test_inputs)) * 1000  # ms

print(f"  Fuzzy match: {iterations * len(test_inputs)} calls in {elapsed:.2f}s")
print(f"  Average: {per_call:.2f}ms per call")
test("fuzzy match < 10ms per call", per_call < 10, f"took {per_call:.2f}ms")

# ========== 5B: Normalization Performance ==========
print("\n=== 5B: Normalization Performance ===")

from core.fuzzy_match import normalize

test_strings = [
    "play music",
    "open notepad",
    "toggle wifi on",
    "set timer for 5 minutes",
    "lets cook dinner",
]

iterations = 10000
start = time.time()
for _ in range(iterations):
    for s in test_strings:
        normalize(s)
elapsed = time.time() - start
per_call = (elapsed / iterations / len(test_strings)) * 1000000  # us

print(f"  Normalization: {iterations * len(test_strings)} calls in {elapsed:.2f}s")
print(f"  Average: {per_call:.2f}us per call")
test("normalization < 100us per call", per_call < 100, f"took {per_call:.2f}us")

# ========== 5C: Router Performance ==========
print("\n=== 5C: Router Performance ===")

from core.router import Router

router = Router(registry, macros)

test_inputs_router = [
    "open notepad",
    "close chrome",
    "open https://github.com",
    "set volume 50",
    "set timer 30s",
]

iterations = 100
start = time.time()
for _ in range(iterations):
    for text in test_inputs_router:
        router.match(text)
elapsed = time.time() - start
per_call = (elapsed / iterations / len(test_inputs_router)) * 1000  # ms

print(f"  Router: {iterations * len(test_inputs_router)} calls in {elapsed:.2f}s")
print(f"  Average: {per_call:.2f}ms per call")
test("router < 5ms per call", per_call < 5, f"took {per_call:.2f}ms")

# ========== 5D: Safety Check Performance ==========
print("\n=== 5D: Safety Check Performance ===")

from core.safety import check_blocked

test_commands = [
    "Get-Date",
    "Start-Process notepad",
    "Get-Process",
    "diskpart",
    "format C:",
]

iterations = 1000
start = time.time()
for _ in range(iterations):
    for cmd in test_commands:
        check_blocked(cmd)
elapsed = time.time() - start
per_call = (elapsed / iterations / len(test_commands)) * 1000000  # us

print(f"  Safety check: {iterations * len(test_commands)} calls in {elapsed:.2f}s")
print(f"  Average: {per_call:.2f}us per call")
test("safety check < 50us per call", per_call < 50, f"took {per_call:.2f}us")

# ========== Summary ==========
print(f"\n{'='*50}")
print(f"Phase 5 Results: {passed} passed, {failed} failed")
if errors:
    print("\nFailed tests:")
    for name, detail in errors:
        print(f"  - {name}: {detail}")
print(f"{'='*50}")
