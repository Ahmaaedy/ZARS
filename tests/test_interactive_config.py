"""Interactive prompts, event queue, and config defaults."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import interactive
from core.config import Config, DEFAULTS

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


# ========== A: interactive.ask ==========
print("\n=== A: interactive.ask ===")

original_fn = interactive._input_fn
try:
    interactive._input_fn = lambda p: "hello"
    test("plain line returned", interactive.ask("q?") == "hello")

    interactive._input_fn = lambda p: "  spaced  "
    test("line is stripped", interactive.ask("q?") == "spaced")

    interactive._input_fn = lambda p: ""
    test("empty falls back to default", interactive.ask("q?", default="n") == "n")
    test("empty with no default returns empty", interactive.ask("q?") == "")

    def boom(p):
        raise KeyboardInterrupt
    interactive._input_fn = boom
    test("interrupt returns None", interactive.ask("q?", default="n") is None)

    responses = iter(["maybe", "SEQUENCE"])
    interactive._input_fn = lambda p: next(responses)
    test("bad choice re-prompts, good choice accepted",
         interactive.ask("q?", default="skill", choices=["skill", "sequence"]) == "sequence")

    responses = iter([""])
    interactive._input_fn = lambda p: next(responses)
    test("empty choice falls back to default",
         interactive.ask("q?", default="skill", choices=["skill", "sequence"]) == "skill")
finally:
    interactive._input_fn = original_fn

# ========== B: event queue ==========
print("\n=== B: event queue ===")

interactive.put_event("line", "first")
interactive.put_event("interrupt", None)
test("events come back FIFO",
     interactive.get_event() == ("line", "first") and
     interactive.get_event() == ("interrupt", None))

# ========== C: config defaults ==========
print("\n=== C: config defaults ===")

cfg = Config(path="nonexistent-config.json")
test("provider default", cfg.provider == "ollama")
test("voice-free defaults intact", not hasattr(cfg, "voice_enabled") and
     not hasattr(cfg, "wake_prefix"))

cfg2 = Config(path="nonexistent-config.json")
cfg.search_roots.append("/tmp")
test("config list defaults not shared between instances",
     cfg2.search_roots == DEFAULTS["search_roots"])
test("config.path recorded", cfg2.path == "nonexistent-config.json")

# ========== Summary ==========
print(f"\n{'=' * 50}")
print(f"Interactive/Config Results: {passed} passed, {failed} failed")
if errors:
    print("\nFailed tests:")
    for name, detail in errors:
        print(f"  - {name}: {detail}")
print(f"{'=' * 50}")
if failed:
    sys.exit(1)
