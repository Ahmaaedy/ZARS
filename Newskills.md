# On-Device AI Assistant — Full Design Notes

## Table of Contents
1. Architecture Overview
2. Shared Conventions
3. Skill: file_find.py
4. Skill: notify_send.py
5. Skill: quickshare_send.py
6. Skill: power_control.py
7. Skill: screenshot_ocr.py
8. Skill: media_control.py
9. Skill: calendar_skill.py
10. Skill: sysdiag.py
11. Skill: opencode_session.py + stt_bridge.py
12. Skill: tts_output.py

---

# On-Device AI Assistant — Architecture Overview

## Core idea
Separate "what CAN I do" (cheap, fast lookup) from "HOW do I do it well" (the model).
The model never touches a raw shell string directly — it only ever selects a
registered action and fills declared parameters.

## 1. Two-tier matching before the model runs
- Don't stuff the full command JSON into context every turn.
- Pre-filter first: keyword/alias match or lightweight embedding similarity
  against command descriptions. Pass only the top 5–10 candidates to the model.
- Dead-simple exact phrases can skip the model entirely via regex/keyword hit.
- Saves latency/compute on-device and reduces the chance the small model
  picks the wrong action.

## 2. Command registry schema
Each entry in the JSON registry includes:
- `name`
- `description` (short — this is the retrieval key, not documentation)
- `trigger_keywords` / aliases
- `param_spec` (name, type, required)
- `execution_type` (skill script / shell template / builtin)
- `confirm_required` flag for destructive actions

## 3. Narrowing the model's job
Given user text + the shortlisted candidates, the model outputs one structured
decision:
```
{ action, target, args }
```
This should be constrained with a JSON-schema/grammar constraint (e.g. GBNF
for llama.cpp, or a library like `outlines`) so a small quantized model can't
emit malformed output. This matters more the smaller the model is.

## 4. Skill contract
All skills (file access, messaging, search, etc.) follow one shared I/O
contract so they're composable and swappable. See `01-shared-conventions.md`.

## 5. Safety layer between decision and execution
- The model only ever selects a registry entry and fills declared params.
- Those params get substituted into a fixed command template — the model
  never constructs the shell string itself. This kills most injection risk.
- Add sandboxing: timeouts, resource limits, whitelist-only execution.

## 6. Response loop
- Simple confirmations → templated response, no model call needed.
- Skill output that needs to sound natural → one more small model pass,
  only when justified (avoid unnecessary inference calls).

## 7. Fallback behavior
- No confident match → open chat mode or "I can't do that yet" rather than
  guessing and running the wrong action.

## Related files
- `01-shared-conventions.md` — skill I/O contract, path allowlisting, permission tiers
- `02-skill-file-find.md` — fuzzy file search skill
- `03-skill-notify-send.md` — Telethon messaging skill
- `04-skill-quickshare-send.md` — Quick Share file transfer skill

---

# Shared Conventions Across Skills

These patterns are used by every skill (`file_find.py`, `notify_send.py`,
`quickshare_send.py`, and future ones) so they stay composable and don't
duplicate safety-critical logic.

## 1. Skill I/O contract
Every skill takes arguments via CLI (`argv`) and always emits structured
JSON on stdout instead of free text. The orchestrator parses this — it never
parses raw/free-form stdout.

Common envelope fields:
```
status        (e.g. "ok" / "ambiguous" / "error" / "unsupported")
result        (skill-specific payload)
display_text  (short human-readable summary, for direct echo-back)
```
Skills add their own fields on top (e.g. `matches`, `target`, `action`) but
keep this shape consistent so the orchestrator's parsing logic doesn't need
per-skill special cases.

## 2. Shared path-resolution / allowlist module
Rather than duplicating this logic in every skill that touches the
filesystem, factor it into one shared module that all relevant skills import.

- Define an allowlist of root directories (e.g. `~/Documents`, `~/Downloads`,
  a dedicated assistant workspace folder).
- Every file operation resolves its path with `os.path.realpath()` /
  `Path.resolve()`, which converts relative → absolute, collapses `..`/`.`,
  and follows symlinks to their real target.
- After resolution, re-check the path still falls inside an allowlisted root
  — this matters because resolving symlinks can otherwise walk you outside
  the sandbox. Anything that resolves outside is rejected/dropped, not just
  deprioritized.
- Size and type guards: cap read size, and gate binary/executable files
  separately (report metadata instead of reading contents).

Skills that touch files (`file_find`, `notify_send`'s `send_file:`, 
`quickshare_send`) all call into this one module instead of reimplementing
the checks.

## 3. Permission / confirmation tiers
Maps onto the `confirm_required` flag in the command registry:

- **Read / list / search** → auto-execute, no confirmation needed.
- **Write / create** → may auto-execute or require confirmation depending on
  target directory.
- **Delete / overwrite / move outside workspace / send to an external
  party** → always confirm, always show the resolved path before acting.

## 4. Keep content out of the router's context
The retrieval/routing step (the part that shortlists candidates for the
model) should only ever see filenames and metadata — never raw file
contents. Contents are pulled in only after a specific action is chosen.
This keeps context small and avoids leaking sensitive data into every
routing decision.

## 5. Secrets never travel through argv
Any credentials (API keys, session files, tokens) live in a config file on
disk with restricted permissions (e.g. `chmod 600`), loaded by the skill
directly — never passed as command-line arguments, since argv is visible in
process lists and logs.

---

# Skill: file_find.py

Fuzzy file lookup by free-text description, returning ranked absolute paths.
Uses shared conventions from `01-shared-conventions.md`.

## 1. Input
- `argv[1]` — free-text query (e.g. "resume", "vacation photos", "budget
  spreadsheet")
- `argv[2]` — optional number of results (default 5)

## 2. Search approach (not `==`)
Walk the allowlisted roots and score every candidate filename against the
query using fuzzy matching (e.g. `rapidfuzz` — fast, C-backed, good for
on-device use). Token-sort or partial-ratio scoring so "budget spreadsheet"
matches something like `Q3_budget_final.xlsx` even out of order.

Blend the fuzzy score with cheap boosts:
- Recency (recently modified ranks higher)
- Exact substring match
- Extension relevance if the query implies a type ("photo" → image
  extensions)

## 3. Live scan vs. cached index
- For modest file counts, a live `os.walk` + score pass per query is often
  fast enough and avoids stale results.
- If directories are large, maintain a lightweight cache (path, name, mtime,
  size) refreshed lazily — only rescan entries whose mtime changed.
- Recommendation: start with live scan; add caching only if latency becomes
  a real problem.

## 4. Path resolution
For every candidate that makes the cut:
- Resolve with `os.path.realpath()` / `Path.resolve()` (absolute, no `..`,
  symlinks followed to real target).
- Re-check the resolved path is still inside an allowlisted root (shared
  module from `01-shared-conventions.md`). Anything that resolves outside is
  silently dropped from results, not just deprioritized.

## 5. Output contract
```
status
query
matches: [ { absolute_path, filename, score, size, modified }, ... ]
display_text
```
Top-N sorted by score, descending.

## 6. Ambiguity handling
If the top score and second score are close (e.g. within ~10% of each
other), return `status: "ambiguous"` instead of picking one — this lets the
orchestrator ask "which one did you mean" rather than silently guessing
wrong. A clear winner returns `status: "ok"` with the best absolute path
plus the rest as alternates.

---

# Skill: notify_send.py

Sends a text message or file to the user via Telethon (Telegram). Uses
shared conventions from `01-shared-conventions.md`.

## 1. Trigger parsing precedence
Check the input string in this order:
- Starts with `send_file:` → strip the prefix, treat the remainder as a
  path (optionally support `send_file:<path>|<caption>` for captions).
- Otherwise → treat the entire string as plain text to send.

Deliberately no automatic "does this look like a path" detection on plain
text — resolving arbitrary text against the filesystem on every message
risks false positives and adds an unnecessary filesystem check to the common
case. Explicit prefix only is more predictable and just as easy for a small
model to emit.

## 2. File path safety
Reuse the shared allowlist + `realpath()` resolution module — don't
duplicate it. Any resolved path outside the allowlisted roots is rejected
before Telethon ever sees it. Also check: file exists, is readable, isn't a
special/device file, and is under Telegram's size limit (2GB standard, lower
for some account tiers). Reject early with a clear error rather than letting
the upload fail mid-transfer.

## 3. Credentials and target — never via argv
- `api_id` / `api_hash` / session file live in a config file on disk with
  restricted permissions (`chmod 600`), loaded by the skill — never passed
  as CLI args (argv is visible in process lists/logs).
- Initial Telethon login (phone number + code) happens once, manually,
  outside the assistant loop. The skill only ever loads an
  already-authorized session.
- The target chat (e.g. your own "Saved Messages" or a specific contact ID)
  is fixed in config, not derived from the model's input. Even if routing
  picks the wrong skill or produces a weird argument, it can't redirect the
  message to someone else.

## 4. Async execution
Telethon's client is async, so the skill wraps a short `asyncio` run:
connect using the existing session → send → disconnect.

Note: this connect/disconnect handshake adds latency per call. If messages
are sent frequently, consider a small persistent daemon that skills talk to
over a local socket instead of spinning up a fresh Telethon client every
invocation (same idea as a DB connection pool vs. reconnecting per query).
Can be deferred until latency is actually a problem.

## 5. Output contract
```
status
action        ("send_text" / "send_file")
target
result
display_text
```
Catch Telethon-specific failures explicitly and map each to a distinct
`status` so the orchestrator can react differently:
- `FloodWaitError` (rate limited — include retry-after seconds, consider
  auto-retry)
- Auth/session errors (surface immediately, don't retry)
- File-too-large (reject before attempting upload)

---

# Skill: quickshare_send.py

Sends a file to a nearby device via Quick Share (Android Nearby Share
protocol). Uses shared conventions from `01-shared-conventions.md`.

## No official API
Google does not expose an official public API for Quick Share. The viable
option is a reverse-engineered library.

## Library: `pyquickshare`
An async Python implementation of the Quick Share / Nearby Share protocol.

Key characteristics:
- Sending currently supports files only (not yet text or WiFi credentials)
  — matches this skill's use case.
- Discovery uses mDNS to find nearby devices; BLE is used only to trigger
  advertisement.
- Transfer currently works over LAN/WiFi only — Bluetooth transfer is
  planned but not yet implemented.
- System requirements: an mDNS implementation (avahi, systemd-resolved,
  etc.) and a Bluetooth stack via BlueZ reachable over D-Bus. These are
  standard on most Linux distros, so it fits a Linux on-device assistant
  well, but is a non-starter on Windows/macOS without extra work.
- Requires incoming connections allowed on the advertised port. It will
  temporarily reconfigure `firewalld` if present, auto-removing the rule
  after 5 minutes — worth knowing since it touches firewall config, not
  just the file.
- Provides a CLI example with `send <file>` and `receive` commands, which
  maps directly onto this skill's shape.

Caution: this is an actively-developing, unofficial reverse-engineered
protocol implementation, not something Google maintains. Pin a specific
version and test transfers against your actual receiving devices before
relying on it — protocol quirks/version mismatches are more likely here
than with an official SDK.

## Skill design

### 1. Path safety
Same allowlist + `realpath()` resolution as the other file-touching skills.
Nothing outside the sandbox gets sent.

### 2. Target device selection
Quick Share needs a receiving device nearby and discoverable. Since there's
no reliable way to address a specific device by fixed ID in an early-stage
library like this, two options:
- (a) A fixed, pre-configured known device, or
- (b) A discovery step that lists nearby advertising devices and asks the
  orchestrator/user to pick.
Don't let the model blindly pick the first device from a live scan.

### 3. Async wrapper
Same pattern as `notify_send.py`: a short `asyncio` run that calls the
library's send function and exits. Consider a persistent daemon if sending
often, since mDNS discovery adds latency per call.

### 4. Output contract
```
status
action
target_device
file
result
display_text
```
Distinguish failure modes explicitly: device not found, firewall/D-Bus
permission denied, transfer rejected on the receiving end, transfer
timeout.

### 5. Platform gate
Since this depends on BlueZ/D-Bus/mDNS, the skill should self-check on
startup and return `status: "unsupported"` if required system services
aren't present, rather than failing deep inside a transfer attempt.

---

# Skill: power_control.py

Lock, sleep, restart, or shut down the machine. Uses shared conventions from
`01-shared-conventions.md`. This is the highest-risk skill in the set —
design leans toward defense in depth rather than a single confirmation gate.

## 1. Input
- `argv[1]` — action: `lock` / `sleep` / `restart` / `shutdown` / `cancel`
- `argv[2]` — optional delay in seconds before executing (default 0 for
  `lock`/`sleep`, default ~60 for `restart`/`shutdown`)

## 2. Platform dispatch
No universal CLI for this across OSes — the skill detects platform and maps
the action accordingly:
- **Linux**: `loginctl lock-session` (lock), `systemctl suspend` (sleep),
  `systemctl reboot` (restart), `systemctl poweroff` (shutdown)
- **macOS**: lock/sleep via `pmset`, restart/shutdown via `osascript`
  targeting System Events
- **Windows**: `rundll32.exe user32.dll,LockWorkStation` (lock), suspend via
  `powrprof.dll` call (sleep), `shutdown /r` / `shutdown /s` with a `/t`
  delay flag (restart/shutdown)

## 3. Confirmation tiers
- `lock` / `sleep` → low risk, auto-execute.
- `restart` / `shutdown` → `confirm_required: true` in the command registry
  **and** a mandatory built-in delay, regardless of upstream confirmation.
  Don't rely on the orchestrator's confirmation step alone for something
  this disruptive and irreversible.

## 4. Delay + cancel window
- Schedule restart/shutdown using the platform's native delay support
  (Windows `shutdown /t <seconds>`; Linux/macOS via a short-lived scheduled
  job) rather than executing instantly.
- Provide a `cancel` action that aborts the pending scheduled action
  (Windows: `shutdown /a`; Linux/macOS: kill the scheduled job).
- If the orchestrator/user explicitly asks for "right now, no delay,"
  that's a valid override — but it should require an explicit instruction,
  not be the default interpretation of "shut down my computer."

## 5. Safety
- Reject negative or unreasonable delay values.
- Log the action with a timestamp before executing — this is one of the few
  irreversible actions in the system.
- If a shutdown/restart is already pending, report that instead of
  scheduling a second one.

## 6. Output contract
```
status
action
platform
scheduled_time   (if delayed)
display_text
```
Distinguish failure modes: unsupported platform, insufficient permissions,
action already pending.

---

# Skill: screenshot_ocr.py

Captures the screen (or active window/region), extracts text via OCR, and
writes the result to the system clipboard. Uses shared conventions from
`01-shared-conventions.md`.

## 1. Input
- `argv[1]` — capture mode: `full` / `active_window` / `region`
- `argv[2]` (if `region`) — coordinates, e.g. `x,y,width,height`
- `argv[3]` — optional: whether to keep the captured image file
  (default: discard after OCR, keep only if explicitly requested)

## 2. Capture
Platform-specific, dispatched like `power_control`:
- **Linux**: `grim` (Wayland) or `scrot`/`import` (X11)
- **macOS**: `screencapture`
- **Windows**: `PIL.ImageGrab` or `pyautogui`

Save to a temp path inside the assistant workspace (allowlisted root) —
never to an arbitrary or permanent location unless the user explicitly asks
to keep the image.

## 3. OCR
- Use `pytesseract` wrapping the Tesseract OCR binary.
- Optional preprocessing (grayscale, thresholding) improves accuracy on
  screenshots with low-contrast text.
- Default language English; accept an optional language code argument for
  other languages.

## 4. Clipboard write
- Use a cross-platform clipboard library (e.g. `pyperclip`), which relies on
  `xclip`/`xsel` on Linux, `pbcopy` on macOS, and native clipboard APIs on
  Windows.
- Write the extracted text directly — this is the primary purpose of the
  skill, not an optional extra.

## 5. Output contract
```
status
mode
extracted_text     (full or truncated preview)
image_path         (if kept)
clipboard_status
display_text
```

## 6. Edge cases
- No text detected → `status: "empty"`.
- Tesseract binary not installed → `status: "unsupported"`, with an install
  hint in `display_text`.
- Clipboard write fails (e.g. headless Linux without `xclip`) →
  `status: "partial"` — text/image were captured but clipboard failed, and
  `extracted_text` is still returned so the orchestrator can show it inline.

## 7. Safety note
Screenshots can capture sensitive on-screen content (passwords, private
messages, personal data) incidentally. Captured images/text should stay in
the workspace sandbox by default. Don't chain this skill's output directly
into `notify_send.py` or any other outbound skill without an explicit,
separate user instruction to send it somewhere.

---

# Skill: media_control.py

Controls the currently active media player (play/pause/skip) and reports
now-playing info. Uses shared conventions from `01-shared-conventions.md`.

## 1. Input
- `argv[1]` — action: `play` / `pause` / `toggle` / `next` / `previous` /
  `now_playing`
- (Note: system-wide volume is handled by a separate `volume_control`
  skill — this skill sticks to media transport controls only, to avoid two
  skills overlapping on the same responsibility.)

## 2. Platform dispatch
- **Linux**: `playerctl` (MPRIS-based) — covers most players (Spotify,
  browsers, VLC) uniformly: `playerctl play/pause/next/previous/status` and
  `playerctl metadata` for now-playing info. This is the cleanest of the
  three platforms since MPRIS gives a standard interface.
- **macOS**: no universal equivalent — target specific apps via `osascript`
  (e.g. Music, Spotify), or simulate global media keys.
- **Windows**: no simple CLI equivalent. Options are (a) simulate global
  media keys (virtual key codes for play/pause/next/previous), which
  controls playback but gives no metadata back, or (b) integrate with the
  SystemMediaTransportControls API for real now-playing info — more setup,
  worth deferring unless now-playing metadata is a priority on Windows.

## 3. Now-playing info
Return track title, artist, and player name where available. Availability
varies a lot by platform: clean on Linux via `playerctl`, limited on
macOS/Windows without deeper API integration.

## 4. Output contract
```
status
action
player            (if identifiable)
now_playing: { title, artist }
display_text
```

## 5. Fallback
If no active media player is detected, return `status: "no_active_player"`
rather than treating it as an error — this is a normal, expected state.

---

# Skill: calendar_skill.py

Adds, lists, and deletes calendar events. Uses shared conventions from
`01-shared-conventions.md`.

## 1. Input
- `argv[1]` — subcommand: `add` / `list` / `delete`
- For `add`: title, start time, end time (or duration), optional
  location/notes
- For `list`: a date range (e.g. `today`, `this_week`, or explicit start/end)
- For `delete`: an event ID, or a fuzzy title + date to match against

## 2. Backend choice
- **Local `.ics` file** (via a library like `icalendar`/`ics`) — fully
  offline, simplest to start with, stored in the assistant workspace.
- **Google Calendar API** — enables sync across devices, but requires OAuth
  token storage. If added later, the token follows the same rule as other
  credentials: stored in a restricted-permission config file, never passed
  via argv.
- Recommendation: start local, treat API sync as a later addition.

## 3. Time input must already be resolved
Natural language time ("tomorrow at 3pm") should be resolved to an explicit
ISO 8601 datetime **before** it reaches this skill — by the orchestrator or
model, not parsed loosely inside the skill itself. This follows the
"narrow the model's job" principle: the skill expects clean, unambiguous
input, not free text it has to interpret.

## 4. Confirmation tiers
- `add` → low risk, auto-execute.
- `list` → read-only, auto-execute.
- `delete` → `confirm_required: true` (destructive).

## 5. Ambiguity handling for delete
If a fuzzy title/date match returns multiple candidate events, return
`status: "ambiguous"` with the candidates — same pattern as `file_find.py`
— rather than guessing which one to delete.

## 6. Conflict detection on add
Optionally check for overlapping events when adding. If found, don't block
the add — return `status: "ok_with_conflict"` along with the conflicting
event, so the orchestrator can mention it to the user rather than silently
double-booking.

## 7. Output contract
```
status
subcommand
events: [ { id, title, start, end, location }, ... ]
display_text
```

---

# Skill: sysdiag.py

Read-only snapshot of process and network status. Uses shared conventions
from `01-shared-conventions.md`.

## 1. Input
- `argv[1]` — scope: `all` / `processes` / `network`
- `argv[2]` — optional process name filter, for a targeted lookup instead of
  a general top-N snapshot

## 2. Process section
- Use `psutil` for cross-platform process enumeration (CPU%, memory%, PID,
  name).
- Don't dump the full process list into the payload — cap to top N by CPU
  and top N by memory (e.g. top 10 each), plus any process matching an
  explicit name filter. Same "don't blow up context" principle used for
  file listings elsewhere in this system.
- On a shared/multi-user machine, filter to the current user's processes by
  default rather than exposing the full system view.

## 3. Network section
- Connectivity check: ping a reliable host or check default gateway
  reachability.
- Active interface info: interface name, IP address, up/down state.
- Optional throughput snapshot via `psutil.net_io_counters()`.
- This is a point-in-time snapshot ("what's my status right now"), not a
  continuous monitor — no background polling or alerting built into this
  skill.

## 4. Combined scope
`scope: all` runs both sections and returns them together. This is useful
as a pre-flight check other skills can call before assuming resources are
available — e.g. checking network status before `quickshare_send.py` or a
web-search skill runs.

## 5. Output contract
```
status
scope
processes: { top_cpu: [...], top_memory: [...], matched: [...] }
network: { connected, interface, ip_address, latency_ms }
display_text
```

## 6. Confirmation tier
Fully read-only → no confirmation needed, always auto-execute.

---

# Skill: opencode_session.py + stt_bridge.py

Opens a terminal window, cd's into a fixed directory, starts an opencode
session, and wires speech-to-text into that session. Uses shared
conventions from the Shared Conventions section above. Unlike the earlier
skills, this pair manages a **persistent, long-running session** rather
than a single stateless call.

## Why two cooperating skills instead of one
- `opencode_session.py` — opens the terminal, cd's in, starts opencode
- `stt_bridge.py` — attaches to that session and injects transcribed speech

Keeping them separate means the STT bridge is reusable for other
terminal-based tools later, instead of being hardwired to opencode.

## 1. Decoupling the visible window from programmatic control
A GUI terminal emulator gives no clean way to inject input into it
programmatically. Fix: run the actual shell inside a `tmux` (or `screen`)
session, then open a visible terminal window that simply *attaches* to that
session. This gives two independent paths into the same session:
- The user sees and can type into it via a normal terminal window
- The skill controls it headlessly via `tmux send-keys -t <session_name>
  "<text>" Enter`

This avoids fragile OS-level keystroke simulation and window-focus
targeting. Native on Linux/macOS; on Windows this needs WSL+tmux, or a more
fragile ConPTY/SendKeys approach.

## 2. Fixed directory — not model-supplied
Same principle as the fixed Telethon target chat: the working directory
lives in config, not as a model-filled argument. Validate it exists via
`realpath()` on startup. If routing ever passes a different path, ignore it
and use the configured one.

## 3. Session lifecycle & state tracking
- On launch, check whether a tmux session with this name is already
  running — if so, don't spawn a duplicate; report it's already active (or
  attach a second window to it).
- Maintain a small state file (session name, PID, start time) so later
  invocations (`status`, `stop`) know what they're addressing.
- A `stop` action kills the tmux session and shuts down the STT bridge
  cleanly.

## 4. STT engine (stt_bridge.py)
- On-device: `faster-whisper` or `whisper.cpp` with a small/base model —
  good latency/accuracy tradeoff, no cloud dependency.
- Push-to-talk or toggle-to-record, not open-mic. An always-listening mic
  feeding an agentic coding tool risks ambient conversation being
  transcribed and submitted as commands.

## 5. Safety-critical: opencode is not a passive text box
opencode can write files and execute code, which changes the risk profile
of STT here versus dictating a text message:
- Show the transcription as a preview (printed to the terminal, or behind a
  confirm keypress) rather than auto-submitting straight to Enter.
- A garbled transcription auto-submitted to an agentic tool with shell
  access is a meaningfully worse failure mode than a garbled text message.
- Support a spoken/typed cancel word to discard a bad transcription before
  it's sent.

## 6. Output contract
```
status         (launched / already_running / stopped / error)
session_name
pid
directory
stt_status     (listening / idle / stopped)
display_text
```
This differs from the earlier stateless skills: a single call doesn't fully
"complete" — it starts something ongoing, so `status` reflects session
state rather than pure success/failure of one action.

---

# Skill: tts_output.py

Reads opencode's terminal output back as speech, so the session becomes a
two-way voice conversation instead of one-way dictation in. Coordinates
directly with `stt_bridge.py` — these two are no longer independent once
both are running against the same session.

## 1. Capturing output
Use `tmux pipe-pane` to stream the pane's output to a file/pipe
continuously, rather than polling `capture-pane` on a timer. Event-driven:
no missed output, no wasted cycles.

## 2. Turn-boundary detection
opencode streams tokens rapidly and produces a lot of transient noise (tool
calls, progress output). Reading every line the instant it appears would be
unintelligible. Instead, buffer output and speak only after a quiet period
(e.g. ~1–2s with no new output) — a rough signal that it's done and it's
the user's turn. Batch to speech; don't stream token-by-token.

## 3. Text cleaning before speech
- Strip ANSI escape/color codes.
- Detect code blocks and substitute a short phrase ("here's the code
  change") instead of reading code character-by-character.
- Skip repeated/boilerplate lines (spinners, progress bars, duplicate
  tool-call logs).

## 4. The core problem: audio feedback loop
If TTS plays through speakers while STT's mic is listening, the mic can
pick up the TTS output and transcribe it right back into the terminal — a
self-triggering loop.

**Fix: half-duplex, not full-duplex.** STT and TTS coordinate through a
shared state (a simple lock file is enough):
- TTS sets a "speaking" flag when playback starts, clears it when done.
- STT checks that flag and suspends listening while it's set.

Walkie-talkie style, not simultaneous open-mic-both-ways. Proper echo
cancellation is possible but a meaningfully bigger lift — worth deferring
unless half-duplex proves too clunky in practice.

## 5. Barge-in (optional)
A hotkey or spoken "stop" to immediately kill in-progress playback, for
when TTS is reading something long and irrelevant.

## 6. Sensitive output
If the terminal displays something like an API key, token, or password
prompt, that shouldn't be read aloud by default. Apply a basic redaction
filter for common secret-looking patterns before text reaches the TTS
engine.

## 7. Engine choice
Piper TTS pairs naturally with `faster-whisper`/`whisper.cpp` on the input
side — both fully offline, both lightweight enough for on-device use.

## 8. Output contract
```
status                (speaking / idle / stopped / error)
session_name
last_spoken_preview
display_text
```

## 9. Shared coordination summary
Both `stt_bridge.py` and `tts_output.py` read/write the same lock file for
turn-taking. This is the one piece of shared state between two otherwise
independent skills — worth documenting clearly wherever the lock file path
is defined, since a bug here causes the feedback loop this design exists to
prevent.
