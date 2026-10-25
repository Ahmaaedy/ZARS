# ZARS — on-device terminal AI assistant.

A terminal-based assistant for Windows that runs commands and Python "skills",
driven by a small local model (Ollama) with a JSON command registry. Common
phrases execute instantly via keyword matching; everything else goes through a
bounded multi-step LLM loop. A safety layer gates every action.

It also runs with no cloud at all: a wake word, a local Whisper, a local model
and a local Kokoro voice, plus an optional animated orb overlay.

## Quick start

```powershell
python main.py                       # interactive REPL
python main.py --dry-run             # print actions without executing
python main.py --no-confirm          # skip confirmation prompts (careful)
python main.py --debug               # verbose model I/O
python main.py --model gemma4:12b    # override model from config.json
python main.py --host http://localhost:11434
python main.py --exec "weather in cairo"   # one-shot request, then exit
python main.py --voice              # voice mode
```

Requires: Python 3.13, `pip install -r requirements.txt`, and Ollama running
with a small instruct model (see `config.json`).

Ollama is started for you when it is not already answering: ZARS probes the
host, runs `ollama serve` if nothing responds, waits for it, and stops it again
on exit -- but only if it was the one that started it. An Ollama you were
already running is never touched. If the configured model was never pulled,
ZARS prints the exact `ollama pull` command instead of failing later with an
opaque 404. Set `"ollama_autostart": false` to opt out.

For skill generation (the macro system), add your OpenRouter API key to `.env`:

```
OPENROUTER_API_KEY=your_key_here
```

## How a request flows

```
input -> Router (tier "core" entries only)
          |  trigger match, token-boundary aware, arg must be present
          |  no match -> macro triggers -> LLM tool-pick (JSON-schema constrained)
        Safety gate
          |  blocklist regexes hard-deny (format, reg delete, Remove-Item -Recurse...)
          |  risk=confirm asks y/N; power actions count down instead
        Executor  (powershell | python skill | builtin)
          |  timeouts, output capture + truncation
        feedback -> model decides: another tool, done (summarize), or ask
        (max step_budget steps, repeats are stopped)
```

## Command registry

`commands.json` holds the built-ins with `shell` / `builtin` handlers.
`skills/` folders are auto-discovered and merged in at startup. Both use the
same entry schema, and both are offered to the model as tools.

### Two tiers

`tier` decides who is allowed to resolve a request without the model:

| tier | resolved by | notes |
|---|---|---|
| `core` | the router, on `triggers` | no LLM call; the fast path |
| `skill` (default) | the model | always goes through the LLM loop |

Single-word verbs -- `open`, `close`, `launch`, `start`, `stop`, `set`, `run`,
`toggle`, `write`, `search`, `remember`, `find` -- are reserved for `core`
entries. A `skill`-tier entry that claims one is rejected by the load-time
linter, which also refuses two entries claiming the same trigger phrase with
the same parameter.

> The built-ins in `commands.json` do not set `tier` yet, so they load as
> `skill` tier and the loader prints one warning at startup
> (`skill 'open_app' cannot claim reserved core verb 'launch'`). Adding
> `"tier": "core"` to each of them puts the built-ins back on the fast path and
> clears the warning. `web_search` is the only entry currently on the fast path.

### Built-ins (`commands.json`)

| id | handler | risk |
|---|---|---|
| open_app | PowerShell | safe |
| close_app | PowerShell | safe |
| open_url | PowerShell | safe |
| write_note | Python builtin | safe |
| search_notes | Python builtin | safe |
| toggle_wifi | PowerShell | safe |
| set_timer | Python builtin | safe |

### Skills (`skills/*/skill.json`)

| id | does | risk |
|---|---|---|
| web_search | search the internet, returns titles, urls and snippets | safe |
| check_weather | current weather plus a 3-day forecast for a location | safe |
| take_screenshot | capture the screen to a PNG file | safe |
| screenshot_ocr | capture the screen, OCR it, copy the text to the clipboard | safe |
| display_audio | set, read or mute volume and screen brightness (0-100) | safe |
| media_control | play, pause, next, previous | safe |
| play_song | play a track on Spotify from the local song library | safe |
| crank that soldier boy | play the specified Spotify track | safe |
| file_find | find files by free-text description (fuzzy match) | safe |
| count_files_in_directory | count the files in a directory | safe |
| pc-stats | CPU/GPU temperatures, memory, disk, fans, processes, drivers | safe |
| fan_control | boost, quiet or toggle the laptop case fans | safe |
| notify_send | send a text message or a file to yourself over Telegram | safe |
| send_file_quickshare | send a file to another device over QuickShare | safe |
| good morning | open a news page | safe |
| lets cook | open apps across several screens and windows | safe |
| create_skill | generate a new skill from a natural-language description | safe |
| close_non_background_apps | close every open app at once | confirm |
| power_control | lock, sleep, hibernate, restart, shut down, cancel a pending action | confirm |
| test_complex_cleanup | free disk space if it falls below a threshold | confirm |
| voice_dictation_skill | record, transcribe, and type until "that's the end" | confirm |

The `test_subtle_*` skills are fixtures for the test suite, not something to
leave in a live registry.

### Registry entry schema

Same shape for `commands.json` and for skill manifests:

```json
{
  "id": "web_search",
  "desc": "search the internet and return top results",
  "params": [{ "name": "query", "type": "string", "required": true }],
  "triggers": ["web search", "google", "look up"],
  "examples": ["search for best chocolate cake recipe"],
  "handler": { "type": "skill", "run": "skills/web_search/skill.py", "timeout": 45 },
  "risk": "safe",
  "tier": "core"
}
```

`triggers` defaults to `keywords`, which is the older spelling and still works.
`id` defaults to the skill folder name, `tier` to `skill`, `risk` to `confirm`.
`risk` is `safe` or `confirm`; the handler is `shell`, `skill` or `builtin`.

## Writing a skill

1. Create `skills/<name>/skill.py` and `skills/<name>/skill.json`.
2. The manifest uses the registry schema above (`id` defaults to folder name).
3. Parse args with `zars_sdk.build_parser`, return results with `zars_sdk.emit`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zars_sdk import build_parser, emit, manifest_for

ns = build_parser(manifest_for(__file__)).parse_args()
emit(True, {"hello": ns.name})
```

Contract: args via `--param value` CLI flags, one JSON line on stdout
`{"ok": true, "data": ...}`. Broken skills fail loudly at startup.

## Slash commands

```
/help                          this help
/commands                      list the loaded command registry
/macro list                    list all macros
/macro add                     start interactive macro definition
/macro delete <name>           delete a macro
/memory                        show the last 5 conversation turns
/memory clear                  clear conversation history
/memory search <query>         search long-term memory
/memory status                 count short-term messages and long-term files
/reload                        re-scan commands.json and skills/
/shell <cmd>                   run a raw PowerShell command (safety-gated)
/dry-run                       toggle dry-run mode
/debug                         toggle verbose model I/O
/clear                         clear screen
/exit                          quit
```

The macro and memory commands also work as plain sentences -- "set a macro",
"list macros", "delete the morning macro" -- so the voice path reaches them
without the slash.

## Macros

`macros.json` holds named shortcuts. A macro is either a **sequence** of
registry calls or a **skill** generated for you.

```
/macro add
  trigger word?     -> "coffee"
  what should it do?-> "open notepad and write a note"
  skill or sequence?-> skill
```

The `skill` route asks OpenRouter (`cohere/north-mini-code:free`) for a
manifest and a `skill.py`, writes them to `skills/<trigger>/`, and registers
the trigger. The `sequence` route chains existing commands instead:

```
step> open_app app_name=notepad
step> write_note content=buy_milk
step> done
```

Steps are `tool_name key=value` pairs, one per line, `done` to finish.

Say the trigger and the macro runs, through the same safety gate as anything
else -- a confirm-risk step in a macro still asks (or, in voice mode, still
asks out loud). Deleting a macro that owns a generated skill removes the skill
folder with it.

## Memory

Three stores, all under `memory/`:

| path | holds |
|---|---|
| `memory/recent.json` | the last `memory_recent` messages of the conversation |
| `memory/conversations/` | one markdown file per turn, with YAML frontmatter |
| `memory/facts/` | long-term facts, searchable |

`/memory` prints the last five turns, `/memory search <query>` greps the
long-term files, `/memory clear` empties the short-term buffer.

## Safety

Every plan -- PowerShell, skill or builtin -- passes the same two lists in
`core/safety.py` before it runs.

**Blocklist** -- hard-denied, with a reason: `diskpart`, `format`,
`clear-disk`/`initialize-disk`, `del /s`, `rd /s`, `rmdir /s`,
`Remove-Item -Recurse`, registry writes, service control, `cipher /w`,
`vssadmin delete`, `bcdedit`, `mkfs`, raw `dd` writes, `net user`,
`set-executionpolicy`, `schtasks /create`.

**Confirm list** -- asks first: shutdown, restart, stop-computer, hibernate,
screen lock, mass app closing, and any command that opens a browser to look
something up. Skills are checked by entry id as well as by their rendered
command, so a skill cannot be a way around it.

Power actions and screen locks do not use the y/N prompt. They **count down**
(`confirm_countdown_seconds`, 10s by default) and run unless you type a
cancel word -- `no`, `n`, `stop`, `cancel`, `nope`, `nah`, `abort` -- in time.
Silence means go, which is only safe because the countdown is restricted to
actions that cannot be undone anyway. Set the count to `0` to restore the
blocking prompt. In voice mode the same window is spoken and listens for
"stop".

Two rejections in a session and ZARS auto-denies everything after that.

## Session log

Every line the terminal shows is mirrored into a plain-text log, in the text
REPL and in voice mode alike:

```
logs/zars-20261019-142300.txt
```

The terminal keeps its colours; the log keeps the same text with the ANSI
escapes stripped, one timestamped entry per line or panel. Your typed request
is recorded too, so the file is the whole conversation. The path is printed at
startup. Logs are gitignored and never cleaned up automatically -- delete the
folder when it gets in the way.

## Offline vs online mode

`ZARS_MODE` in `.env` (see `.env.example`) picks the backend:

| Stage | `offline` (default) | `groq` | `gemini` |
|---|---|---|---|
| Wake word | openWakeWord (local) | same | same |
| VAD | Silero (local) | same | same |
| STT | faster-whisper (local) | Groq Whisper | Groq Whisper |
| Reasoning | Ollama (local) | Groq chat | Gemini chat |
| TTS | Kokoro (local) | same | same |
| Router, tools, notes, memory | local | same | same |

TTS is always on-device. STT stays on Groq in **both** online modes -- Gemini is
used for reasoning only. Nothing streams while idle: audio is only sent after
the wake word fires and the VAD confirms speech.

**Cross-cloud fallback.** In `groq` mode the reasoning chain is
Groq -> Gemini -> local Ollama; in `gemini` mode it is Gemini -> Groq -> local
Ollama. Either way the assistant keeps answering if one cloud is down, and
falls back to fully local last. A cloud with no API key is skipped silently.

```powershell
python main.py --voice                  # offline (default)
$env:ZARS_MODE="gemini"; python main.py --voice   # one shell only
```

`ZARS_MODE=online` is still accepted as a deprecated alias for `groq`. An
unknown value, or a missing API key for the selected mode, logs a warning and
runs offline. With `ONLINE_FALLBACK_TO_LOCAL=false` only the selected provider
is used -- no cloud-to-cloud, no local.

In online mode ZARS prints a notice at startup: recorded audio, the last 10
conversation turns, and tool output are sent to your cloud provider.

The CLI honours `ZARS_MODE` too, not just the voice path.

## Voice mode (`--voice`)

```
idle --wake word / hotkey--> listen --VAD endpoint--> STT --> LLM --> TTS --> idle
```

Fully offline by default. Everything runs on CPU; no cloud service is
contacted.

| stage | model | where it comes from |
|---|---|---|
| wake word | `hey_rhasspy` (openWakeWord, ONNX) | `models/wakeword/` |
| endpointing | Silero VAD (ONNX) | `models/vad/` |
| STT | faster-whisper tiny, int8 | `voice_whisper_model` (local dir) |
| TTS | Kokoro-82M (ONNX) | `models/tts/` |

Only the wake-word model is resident while idle. STT and TTS load on first use,
or stay warm when `voice_keep_warm` is true. The mic is muted while speaking
and for `voice_cooldown_s` afterwards, so the assistant cannot trigger its own
wake word. Any failure (no mic, missing model, empty transcript) is logged and
the loop returns to idle.

### Waking up

Two ways in, plus a sleep policy:

- **Wake word** -- say it, and a chime plays so you know it was heard.
- **Hotkey** -- `voice_wake_hotkey` (default ``alt+` ``) wakes her without
  talking. Registered with Windows, so no admin rights, and released the
  moment voice mode exits. If another app already owns the combination, voice
  mode says so and carries on with the wake word.

Then the **sleep policy** decides what happens after she answers:
`voice_always_asleep` (default) means one request per wake -- she answers,
then goes dormant and ignores everything, including further wake words, until
`voice_sleep_after_answer_s` has passed. The microphone is never closed,
because a closed mic cannot hear a wake word; frames are read and discarded
while she sleeps.

### Speaking and listening

Confirmation prompts are spoken, not printed: "Are you sure you want to run
this? That will ... Say yes or no." The assistant fails closed -- if the
prompt cannot be spoken, or the answer cannot be read as yes or no, the action
is denied. The one exception is the countdown for power actions, where silence
means go.

### The orb

`--voice` starts a translucent animated orb in a separate process
(`animations/orb_overlay.py`, needs PyQt5) anchored bottom-left, with a
speech caption bubble above it that types out what she is saying in time with
the audio. The overlay is decoration: if it crashes or cannot start, voice mode
carries on without it. Set `"voice_orb_enabled": false` to skip it.

### Audio devices

Devices are chosen automatically: candidates are filtered by whether they can
run at the rate ZARS needs, then ranked by host API with MME last (the legacy
`waveOut` API returns "no driver installed" for virtual and USB endpoints).
Pin a device in `config.json` with `voice_mic_device` or
`voice_speaker_device` if the automatic pick is not the hardware you want.
`voice_audio_host_api` overrides the host ranking.

### Memory

Conversation memory is shared with the CLI. After `voice_session_reset_s` of
silence the *in-memory* voice context clears; `memory/recent.json` and the CLI
history are left untouched.

Check each stage on its own:

```
python test_voice.py mic         # devices + 3s level check
python test_voice.py wake        # live wake-word scores
python test_voice.py vad         # live speech/silence decisions
python test_voice.py stt --wav file.wav
python test_voice.py tts         # speak one sentence
python test_voice.py tts-stream  # streamed sentence playback
python test_voice.py agent       # one full mic -> STT -> LLM -> TTS cycle
python test_voice.py all         # every stage in one go
```

## Configuration (`config.json`)

Precedence: built-in defaults < `config.json` < the `.env` file < the process
environment. A real environment variable beats the file, so
`ZARS_MODE=gemini python main.py --voice` works for a single run.

| key | meaning |
|---|---|
| model | Ollama model used for tool-picking |
| host | Ollama host |
| temperature | keep low (0.1) for reliable tool selection |
| num_ctx | model context window |
| step_budget | max model actions per request |
| output_head / output_tail | truncation of tool output fed back to the model |
| shell_timeout / skill_timeout | seconds |
| notes_dir | where write_note/search_notes store notes |
| memory_dir / memory_recent | where the memory stores live, and how many messages to keep |
| search_roots | folders file_find is allowed to search |
| telegram_config | path to the Telegram credentials (notify_send reads `config/telegram.json`) |
| ollama_autostart / ollama_autostart_wait_s | start `ollama serve` when nothing answers, and how long to wait |
| confirm_countdown_seconds / confirm_cancel_words | grace period for irreversible actions, and the words that cancel it |
| zars_mode | offline, groq or gemini |
| groq_* / gemini_* | model ids, timeouts, retry policy for the online providers |
| online_fallback_to_local | allow the cloud -> cloud -> local chain |
| voice_* | everything in the voice pipeline: devices, wake word, VAD, STT, TTS, sleep policy, chime, orb |

The voice keys are worth calling out: `voice_wake_hotkey`, `voice_wake_chime`
and `voice_wake_chime_gain` (the file is normalised to that peak on load, so
any drop-in plays at the same level), `voice_always_asleep`,
`voice_sleep_after_answer_s`, `voice_wakeword_threshold`,
`voice_wake_rearm_ratio`, `voice_wake_max_lock_s`, `voice_wake_cooldown_s`,
`voice_session_reset_s`, `voice_keep_warm`, and the `voice_orb_*` caption
keys.

Most of them can also be set in `.env` -- see `.env.example` for the full list
of `ZARS_*` variables.

## Diagnostics

```
python test_gemini.py models    what your Gemini key can actually reach
python test_gemini.py env       resolved mode + config (never prints the key)
python test_gemini.py llm       stream one reply as a tool pick
python test_gemini.py failures  bad key / no network / fallback chain
python test_gemini.py all       every check in one go
python test_groq.py stt         transcribe a generated sample wav
python test_groq.py llm         stream a Groq reply chunk by chunk
python test_groq.py failures    bad key / no network / 429 / fallback-to-local
python test_voice.py all        every voice stage in one go
python -m pytest tests/ -q      the unit and integration suite
```

`test_groq.py`, `test_gemini.py` and `test_voice.py` all take a stage name and
`all`. None of them prints your keys, and the provider scripts need no
microphone -- the `mic`, `wake`, `vad` and `agent` voice stages do.

## Project layout

```
main.py              entry point: REPL, --exec, --voice
commands.json        built-in command registry
config.json          runtime configuration
config/telegram.json Telegram credentials (gitignored)
core/                agent, router, safety, registry, executor, memory
core/providers/     Ollama / Groq / Gemini providers and the fallback chain
core/voice/          wake word, VAD, STT, TTS, devices, hotkey, orb client
skills/              one folder per skill: skill.json + skill.py
animations/          the orb overlay and its engine
macros.json          user-defined macros (gitignored)
memory/              conversation history and long-term facts (gitignored)
notes/               saved notes (gitignored)
logs/                one plain-text transcript per session (gitignored)
models/              local voice models (gitignored, ~340 MB)
zars_sdk.py          the helper skills import for args and output
```
