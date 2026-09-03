# ZARS — Project Outline & Issues

## What is ZARS?

ZARS is a terminal-based AI assistant for Windows that executes system commands and Python "skills" via a local Ollama LLM. It routes user requests through either a fast keyword-match path or an LLM-driven tool-selection loop, runs the chosen command (PowerShell, Python skill, or builtin), and feeds results back for multi-step reasoning.

### Architecture

```
input -> Router
          |  1. keyword fast-path (no LLM)  e.g. "screenshot", "set volume 30"
          |  2. compound/unknown -> LLM tool-pick (JSON-schema constrained)
        Safety gate
          |  blocklist regexes hard-deny
          |  risk=confirm asks y/n; risk=safe auto-runs
        Executor  (powershell | python skill | builtin)
          |  timeouts, output capture + truncation
        feedback -> model decides: another tool, done (summarize), or ask
        (max step_budget steps, repeats are stopped)
```

### Key Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point — REPL, slash commands, arg parsing |
| `core/agent.py` | Orchestrator — routing, safety, LLM loop |
| `core/router.py` | Keyword matching and param extraction |
| `core/llm.py` | Ollama client, system prompt, JSON schema |
| `core/executor.py` | Runs shell/skill/builtin handlers |
| `core/safety.py` | Blocklist regex for dangerous commands |
| `core/registry.py` | Loads commands.json + skills/ into Entry objects |
| `core/builtins.py` | set_volume, write_note, search_notes, set_timer |
| `core/macros.py` | User-defined macro system (sequence or skill) |
| `core/skillgen.py` | LLM-powered skill code generation |
| `core/session.py` | Chat message history wrapper |
| `core/config.py` | Loads config.json with defaults |
| `zars_sdk.py` | SDK for skills — arg parsing, emit output |
| `commands.json` | Built-in command registry |
| `skills/` | Auto-discovered skill folders |

### Maturity

Early prototype. 2 git commits, most source files untracked, no test suite, hardcoded secrets in the tree. Core loop works, keyword routing is functional, LLM multi-step chaining is demonstrated. Needs hardening, tests, and git cleanup.

---

## Issues

### HIGH

#### 1. `"open notepad"` matches `lets cook` instead of `open_app`
- **File:** `skills/lets cook/skill.json:19` — keyword `"open"` is too generic
- **File:** `core/router.py:18` — keywords sorted by length, `"open"` (4 chars) matches before `"launch"` (6 chars) from `open_app`
- **Impact:** Every `open <app>` request goes to the wrong handler
- **Fix:** Remove `"open"` from `lets cook` keywords, or add more specific keywords like `"lets cook"`, `"setup workspace"`

#### 2. `"open https://..."` drops the protocol prefix
- **File:** `core/router.py:36` — `_fill` takes text after keyword match end
- **Detail:** Keyword `"open https"` matches, then `_fill` gets `"://github.com"` — the `https:` is consumed by the keyword match
- **Impact:** All `open https://...` URLs are broken
- **Fix:** Add `"open http"` and `"open https"` as keywords (already exist in `commands.json:24`), but the real issue is that `"open https"` is 10 chars and `"open"` is 4 chars. Since keywords are sorted longest-first, `"open https"` should match first. Need to debug why it doesn't — likely the regex `(?<!\w)` boundary is interfering with `://`

#### 3. `cleanup` macro is `"risk": "safe"` — kills all foreground processes instantly
- **File:** `skills/cleanup/skill.json:9` — `"risk": "safe"`
- **File:** `skills/cleanup/skill.py:11-24` — `taskkill /F /IM` on every process with a window title
- **Impact:** Kills VS Code, browser, Discord, Explorer, terminal — 17 processes, no confirmation
- **Fix:** Change to `"risk": "confirm"`, or better yet, add a whitelist of safe-to-kill processes

#### 4. API key exposed in source tree
- **File:** `justdoinshi.py:6` — hardcoded OpenRouter API key
- **File:** `.env:3` — same key (gitignored but still in project)
- **Impact:** Key is one accidental `git add` away from being committed
- **Fix:** Delete `justdoinshi.py`, rotate the key

#### 5. Almost nothing is version-controlled
- **Detail:** `git ls-files` shows only `README.md`, `commands.json`, `scrap.py`, `bs.py`, `subp.py`. The entire `core/`, `main.py`, `skills/`, `zars_sdk.py`, `config.json` are untracked
- **Fix:** `git add` all relevant files, remove dead files, commit

### MEDIUM

#### 6. LLM repeats actions when tool output is empty (dry-run)
- **File:** `core/agent.py:198-258` — `_llm_loop`
- **Detail:** In dry-run mode, tools return empty output. The model doesn't see data and re-runs the same tool until step budget is exhausted
- **Impact:** Dry-run is useless for compound requests
- **Fix:** In dry-run mode, inject a synthetic "would succeed" message as the tool result instead of empty string

#### 7. LLM wastes step budget opening URLs instead of summarizing
- **File:** `core/llm.py:25-63` — system prompt doesn't guide model to summarize after search
- **Detail:** "weather + restaurants" used all 4 steps opening links instead of summarizing
- **Fix:** Add prompt instruction: "After searching, summarize results for the user. Don't open every link."

#### 8. `"toggle wifi"` doesn't match via keyword
- **File:** `commands.json:60` — keyword `"toggle wifi"`
- **Detail:** `"wifi on"` and `"wifi off"` work, but `"toggle wifi"` falls through to LLM
- **Fix:** Check if the DEFER_MARKERS list or normalization is interfering

#### 9. `lets cook` skill doesn't actually position windows
- **File:** `skills/lets cook/skill.py:10-17` — declares `$secondary`/`$main` but never uses them
- **Impact:** Description says "Brave on secondary screen, notes on first half" but just launches apps without positioning
- **Fix:** Implement Win32 window positioning or update the manifest description

#### 10. `skillgen.py` function named `_call_opencode` actually calls Ollama
- **File:** `core/skillgen.py:66` — uses `ollama.Client`
- **Impact:** Misleading name, also `opencode-py` is listed as a dependency in README but isn't used
- **Fix:** Rename to `_call_ollama`, remove `opencode-py` from requirements

#### 11. Hardcoded absolute paths in `macros.json`
- **File:** `macros.json:7,14,21` — `C:\\Users\\AceNo\\...`
- **Impact:** Breaks on any other machine
- **Fix:** Store relative paths, resolve at runtime

#### 12. `render_template` only blocks double-quotes
- **File:** `core/registry.py:150-151`
- **Impact:** Doesn't prevent backtick escapes, `$()` subexpressions, or other PowerShell injection vectors
- **Fix:** Add stricter sanitizer or use parameterized execution

#### 13. `_llm_loop` can crash on unknown tool
- **File:** `core/agent.py:236` — `self.registry.entries[tool]` raises `KeyError`
- **Impact:** If registry is reloaded between LLM validation and execution
- **Fix:** Add guard: `if tool not in self.registry.entries`

#### 14. Inconsistent model configuration
- **Detail:** `config.json` = `zars-local`, `config.py` default = `qwen2.5:3b-instruct`, `skillgen.py` = `gemma4:12b`
- **Fix:** Centralize model config

#### 15. `generate_full.py:22` has bare `except:`
- **File:** `generate_full.py:22`
- **Fix:** Change to `except json.JSONDecodeError:`

### LOW

#### 16. No tests exist
- `test_generation.py` and `test_skill.py` are ad-hoc scripts, not automated tests
- **Fix:** Add pytest tests for router, safety, builtins, config

#### 17. Duplicate example in system prompt
- **File:** `core/llm.py:47-48` and `core/llm.py:51-52` — identical `"what is 2+2"` example
- **Fix:** Remove the duplicate

#### 18. `STRIP_LEAD` in router strips too aggressively
- **File:** `core/router.py:3` — `"in "`, `"on "` etc. can strip meaningful text
- **Detail:** "install vscode" → "stall vscode"
- **Fix:** Only strip at the very start of remaining text, or use more specific patterns

#### 19. `web_search` scrapes HTML with regex
- **File:** `skills/web_search/skill.py:37-42`
- **Impact:** Fragile, will break when DuckDuckGo changes HTML
- **Fix:** Use an official search API

#### 20. Orphaned files at project root
- `skill.json` — no matching `skill.py`
- `test_skill.py` — not valid Python (contains LLM prose + markdown)
- `justdoinshi.py` — standalone experiment
- `scrap.py`, `bs.py`, `subp.py` — old files still in git
- **Fix:** Delete or organize

#### 21. `web_search` skill missing early return after error
- **File:** `skills/web_search/skill.py:55-56` — `emit(False, ...)` without `return`
- **Impact:** `emit` calls `sys.exit(1)` so it works, but fragile if exit is caught
- **Fix:** Add `return` after each `emit(False, ...)`

#### 22. `macros.json` should be gitignored
- Contains user-specific data and absolute paths
- **Fix:** Add to `.gitignore`

---

## Quick Wins

| # | Change | Impact |
|---|--------|--------|
| 1 | `git add` all files, remove dead `scrap.py`/`bs.py`/`subp.py`, commit | Recovers all work from the untracked abyss |
| 2 | Delete `justdoinshi.py` and rotate the exposed API key | Closes the secret leak |
| 3 | Remove `"open"` from `lets cook` keywords | Fixes `open notepad` routing to wrong handler |
| 4 | Change `cleanup` skill risk to `"confirm"` | Prevents accidental mass process killing |
| 5 | Add `return` after each `emit(False, ...)` in `skills/web_search/skill.py` | Prevents potential double-emission bug |
| 6 | Delete orphaned `skill.json` and `test_skill.py` at root | Removes confusing dead files |
| 7 | Remove duplicate example in `core/llm.py` | Cleaner system prompt |
