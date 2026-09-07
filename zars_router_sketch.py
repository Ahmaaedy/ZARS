"""
ZARS router redesign sketch -- two-tier routing (Tier 0 deterministic / Tier 1 LLM tool-pick)

This is a standalone, adaptable sketch, not a drop-in replacement for the real
core/router.py + core/registry.py + core/llm.py. It illustrates the design from
the conversation:

  - Skills can NEVER enter the deterministic fast path (Tier 0). Only curated
    "core" commands can. This is what makes the original bug (a skill's
    generic "open" keyword shadowing open_app) structurally impossible.
  - Tier 0 matching is token-boundary aware (not substring), so a trigger like
    "open" can never eat part of the next token (fixes the "open https://..."
    bug from the issue list).
  - A match isn't final until its required argument actually exists -- an
    "open" match with nothing left over doesn't count as a match.
  - The registry loader lints the whole trigger table at load time and refuses
    to start if a skill tries to claim a reserved single-word verb, or if two
    entries claim the exact same trigger phrase.
  - Everything that isn't resolved by Tier 0 -- every skill, unconditionally --
    goes to Tier 1: the LLM, given a JSON-schema tool manifest built straight
    from the registry.

Wire-up notes for the real codebase:
  - TriggerEntry.tier / .requires_arg / .description map onto whatever fields
    you already keep in commands.json / skill.json manifests -- this sketch
    just makes those fields load-bearing instead of decorative.
  - `route()`'s `llm_tool_pick` param is where core/llm.py's actual Ollama
    call goes; here it's a plain callable so the sketch runs with no
    dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 1. Trigger schema -- replaces bare keyword strings with a typed declaration
# ---------------------------------------------------------------------------

class TriggerTier(str, Enum):
    CORE = "core"    # curated, safety/frequency-critical, eligible for Tier 0
    SKILL = "skill"  # user-defined, ALWAYS routed through the LLM (Tier 1)


# Single-token verbs no skill is allowed to claim on its own.
RESERVED_VERBS = frozenset({
    "open", "close", "launch", "start", "stop", "set", "run", "toggle",
})


@dataclass
class TriggerEntry:
    intent: str
    tier: TriggerTier
    triggers: List[str]                 # phrases, e.g. ["open", "launch"]
    description: str                    # shown to the LLM in Tier 1
    requires_arg: Optional[str] = None  # e.g. "app_name", or None
    arg_description: str = ""


class RegistryConflictError(Exception):
    """Raised at load time -- refuses to start rather than misroute silently."""


# ---------------------------------------------------------------------------
# 2. Registry loader -- the load-time linter that replaces "hope nothing collides"
# ---------------------------------------------------------------------------

def load_registry(entries: List[TriggerEntry]) -> Dict[str, TriggerEntry]:
    registry: Dict[str, TriggerEntry] = {}
    phrase_owner: Dict[str, str] = {}

    for entry in entries:
        for trigger in entry.triggers:
            norm = trigger.strip().lower()
            tokens = norm.split()

            if entry.tier is TriggerTier.SKILL and len(tokens) == 1 and tokens[0] in RESERVED_VERBS:
                raise RegistryConflictError(
                    f"skill '{entry.intent}' cannot claim reserved core verb "
                    f"'{tokens[0]}' -- use a multi-word phrase instead"
                )

            if norm in phrase_owner and phrase_owner[norm] != entry.intent:
                raise RegistryConflictError(
                    f"trigger '{trigger}' is claimed by both "
                    f"'{phrase_owner[norm]}' and '{entry.intent}'"
                )
            phrase_owner[norm] = entry.intent

        registry[entry.intent] = entry

    return registry


# ---------------------------------------------------------------------------
# 3. Tokenizer -- whitespace-only, span-aware, so matching never slices
#    mid-token (this is what fixes the "open https://..." truncation bug)
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> List[Tuple[str, int, int]]:
    tokens = []
    i = 0
    for raw in text.split(" "):
        start = text.index(raw, i)
        end = start + len(raw)
        if raw:
            tokens.append((raw.lower(), start, end))
        i = end + 1
    return tokens


# ---------------------------------------------------------------------------
# 4. Tier 0 -- deterministic fast path, CORE entries only
# ---------------------------------------------------------------------------

@dataclass
class RouteResult:
    intent: str
    arg: str = ""
    via: str = "tier0"  # or "tier1"


def _match_core(text: str, tokens: List[Tuple[str, int, int]],
                 core_entries: List[TriggerEntry]) -> Optional[RouteResult]:
    best: Optional[Tuple[int, TriggerEntry, int]] = None  # (n_tokens, entry, end_idx)

    for entry in core_entries:
        for trigger in entry.triggers:
            trig_tokens = trigger.lower().split()
            n = len(trig_tokens)
            if n > len(tokens):
                continue
            if [t[0] for t in tokens[:n]] != trig_tokens:
                continue
            candidate = (n, entry, tokens[n - 1][2])
            if best is None or candidate[0] > best[0]:
                best = candidate

    if best is None:
        return None

    _, entry, end_idx = best
    arg = text[end_idx:].strip()

    # A match without its required argument doesn't count as a match --
    # falls through to Tier 1 instead of firing on garbage input.
    if entry.requires_arg and not arg:
        return None

    return RouteResult(intent=entry.intent, arg=arg, via="tier0")


# ---------------------------------------------------------------------------
# 5. Tier 1 -- JSON-schema tool manifest for the LLM, built from the registry
# ---------------------------------------------------------------------------

def build_tool_manifest(entries: List[TriggerEntry]) -> List[dict]:
    manifest = []
    for entry in entries:
        props: dict = {}
        required: List[str] = []
        if entry.requires_arg:
            props[entry.requires_arg] = {
                "type": "string",
                "description": entry.arg_description or f"The {entry.requires_arg}.",
            }
            required.append(entry.requires_arg)
        manifest.append({
            "name": entry.intent,
            "description": entry.description,
            "input_schema": {"type": "object", "properties": props, "required": required},
        })
    return manifest


# ---------------------------------------------------------------------------
# 6. Top-level router
# ---------------------------------------------------------------------------

def route(text: str, registry: Dict[str, TriggerEntry],
          llm_tool_pick: Callable[[str, List[dict]], RouteResult]) -> RouteResult:
    tokens = _tokenize(text)
    core_entries = [e for e in registry.values() if e.tier is TriggerTier.CORE]

    tier0 = _match_core(text, tokens, core_entries)
    if tier0 is not None:
        return tier0  # no LLM call at all

    # Everything else -- every skill, unconditionally -- goes through the model.
    manifest = build_tool_manifest(list(registry.values()))
    result = llm_tool_pick(text, manifest)
    result.via = "tier1"
    return result


# ---------------------------------------------------------------------------
# 7. Demo -- reproduces the original bug and shows it's now structurally impossible
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    entries = [
        TriggerEntry("open_app", TriggerTier.CORE, ["open", "launch"],
                     "Open/launch an application by name.", requires_arg="app_name",
                     arg_description="Name of the app to open, e.g. 'notepad'."),
        TriggerEntry("set_volume", TriggerTier.CORE, ["set volume", "volume"],
                     "Set system volume to a percentage.", requires_arg="level"),
        TriggerEntry("lets_cook", TriggerTier.SKILL, ["lets cook", "setup workspace"],
                     "Launch the user's coding workspace: editor + browser + notes."),
    ]
    registry = load_registry(entries)  # would raise if lets_cook still had "open"

    def fake_llm_tool_pick(text: str, manifest: List[dict]) -> RouteResult:
        names = [t["name"] for t in manifest]
        print(f"  [Tier 1] LLM sees tools: {names}")
        return RouteResult(intent="lets_cook", arg="")

    for query in ["open notepad", "open https://github.com", "lets cook"]:
        print(f"'{query}' ->")
        result = route(query, registry, fake_llm_tool_pick)
        print(f"  {result}\n")

    print("--- Loader refuses to start if a skill shadows a reserved verb ---")
    try:
        load_registry(entries + [TriggerEntry("bad_skill", TriggerTier.SKILL, ["open"], "x")])
    except RegistryConflictError as e:
        print(f"  RegistryConflictError: {e}")