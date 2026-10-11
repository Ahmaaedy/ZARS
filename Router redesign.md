# ZARS Router Redesign — Plan

## Problem

The current router resolves user input by substring-matching keyword strings
across a flat, untyped table (`commands.json` + every `skills/*/skill.json`),
sorted by string length. That's the root cause of issue #1 (`lets cook`'s
generic `"open"` keyword shadowing `open_app`) — and the same flaw shows up
as #2 (protocol prefix swallowed), #8 (`toggle wifi` falls through), and #18
(aggressive lead-stripping). Patching each symptom individually doesn't stop
the next one from happening.

## Goal

Make this class of bug structurally impossible, not just fixed case-by-case.

## New plan: two-tier routing

### Tier 0 — deterministic fast path, core commands only

- Only a curated set of core commands (`open_app`, volume, wifi, shutdown,
  etc.) are eligible for this tier.
- Skills can never register here — enforced at load time, not by convention.
- Matching is token-boundary aware, not raw substring, so a trigger can never
  eat part of the following token.
- A match only counts once its required argument is actually present after
  the match — an `"open"` match with nothing left over falls through instead
  of firing on garbage.

### Tier 1 — LLM tool-pick, everything else, unconditionally

- Every skill, and anything Tier 0 didn't resolve, always goes to the model.
- The tool manifest (JSON schema) is generated directly from the registry
  instead of hand-maintained separately.
- This removes the "compound/unknown" special case from today's design —
  it's simply the default for this whole category, not a fallback.

## Supporting pieces

1. **Typed trigger schema** — each entry declares `tier`, `triggers`,
   `requires_arg`, and `description` instead of a bare keyword string.
2. **Reserved verb namespace** — a small set of single-token verbs (`open`,
   `close`, `launch`, `start`, `stop`, `set`, `run`, `toggle`) can only belong
   to `tier: core` entries.
3. **Load-time conflict linter** — the registry loader checks the full
   trigger table on startup and refuses to boot on a reserved-verb violation
   or a duplicate trigger phrase, naming both offending entries.
4. **Trigger regression suite** — a fixed table of `(input, expected intent)`
   pairs, run on every load/CI pass, so a future skill addition that breaks
   routing fails a test instead of shipping.

## What this replaces

| Today | New plan |
|---|---|
| `core/router.py` substring/length-sort matching | Tokenized Tier 0 matcher, core-only |
| `core/registry.py` loader | Same loader + the conflict linter |
| `core/llm.py` hand-maintained manifest | Manifest generated from the registry |
| `core/agent.py` "keyword fast-path → LLM tool-pick" | Same shape, but the branch condition becomes "Tier 0 matched a core command" instead of "some keyword matched" |

## What doesn't change

`core/safety.py`'s blocklist + risk-confirm gate still sits after resolution,
regardless of which tier produced the match.

## Known trade-off

Tier 1 calls are slower and non-deterministic compared to a string match.
That's acceptable for skills — lower frequency, less latency-sensitive —
which is why only core commands are allowed into Tier 0 rather than routing
everything through the LLM.

## Reference implementation

A verified, runnable sketch of this design — trigger schema, loader + linter,
tokenizer, Tier 0 matcher, JSON-schema manifest builder, and a demo that
reproduces the original bug and shows the loader rejecting it — is in
`zars_router_sketch.py`.