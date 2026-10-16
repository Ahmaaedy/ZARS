# ZARS — on-device terminal AI assistant

A terminal-based assistant for Windows that runs commands and Python "skills",
driven by a small local model (Ollama) with a JSON command registry. Common
phrases execute instantly via keyword matching; everything else goes through a
bounded multi-step LLM loop. A safety layer gates every action.

## Quick start

```powershell
python main.py                       # interactive REPL
python main.py --dry-run             # print actions without executing
python main.py --no-confirm          # skip confirmation prompts (careful)
python main.py --model gemma4:12b    # override model from config.json
python main.py --exec "weather in cairo"   # one-shot request, then exit
python main.py --voice              # offline voice mode
```

Requires: Python 3.10+, `pip install -r requirements.txt`, and Ollama running
with a small instruct model (see `config.json`).

For skill generation (macro system), add your OpenRouter API key to `.env`:
```
OPENROUTER_API_KEY=your_key_here
```

## How a request flows

```
input -> Router
          |  1. keyword fast-path (no LLM)  e.g. "screenshot", "set volume 30"
          |  2. compound/unknown -> LLM tool-pick (JSON-schema constrained)
        Safety gate
          |  blocklist regexes hard-deny (format, reg delete, Remove-Item -Recurse...)
          |  risk=confirm asks y/n; risk=safe auto-runs
        Executor  (powershell | python skill | builtin)
          |  timeouts, output capture + truncation
        feedback -> model decides: another tool, done (summarize), or ask
        (max step_budget steps, repeats are stopped)
```

## Commands (registry)

`commands.json` holds built-ins with `shell` / `builtin` handlers.
`skills/` folders are auto-discovered and merged in at startup.

| id | handler | risk |
|---|---|---|
| open_app / close_app / open_url | PowerShell | safe / confirm / safe |
| write_note / search_notes | Python builtins | safe |
| toggle_wifi | PowerShell | confirm |
| web_search | skill | safe |
| take_screenshot | skill | safe |
| check_weather | skill | safe |

Registry entry schema (same shape for skill manifests):

```json
{
  "id": "web_search",
  "desc": "search the internet and return top results",
  "params": [{ "name": "query", "type": "string", "required": true }],
  "keywords": ["search", "google", "look up"],
  "examples": ["search for best chocolate cake recipe"],
  "handler": { "type": "skill", "run": "skills/web_search/skill.py", "timeout": 45 },
  "risk": "safe"
}
```

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

`/help` `/commands` `/reload` `/shell <cmd>` `/dry-run` `/debug` `/clear` `/exit`

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

Test the providers without a microphone:

```
python test_gemini.py models    what your Gemini key can actually reach
python test_gemini.py env       resolved mode + config (never prints the key)
python test_gemini.py llm       stream one reply as a tool pick
python test_gemini.py failures  bad key / no network / fallback chain
python test_groq.py stt         transcribe a generated sample wav
python test_groq.py llm         stream a Groq reply chunk by chunk
python test_groq.py failures    bad key / no network / 429 / fallback-to-local
```

## Configuration (`config.json`)

| key | meaning |
|---|---|
| model | Ollama model used for tool-picking |
| host | Ollama host |
| temperature | keep low (0.1) for reliable tool selection |
| step_budget | max model actions per request |
| output_head / output_tail | truncation of tool output fed back to the model |
| shell_timeout / skill_timeout | seconds |
| notes_dir | where write_note/search_notes store notes |
| num_ctx | model context window |

## Voice mode (`--voice`)

```
idle --wake word--> listen --VAD endpoint--> faster-whisper --> LLM --> Kokoro --> idle
```

Fully offline. Everything runs on CPU; no cloud service is contacted.

| stage | model | where it comes from |
|---|---|---|
| wake word | `hey_rhasspy` (openWakeWord, ONNX) | `models/wakeword/` |
| endpointing | Silero VAD (ONNX) | `models/vad/` |
| STT | faster-whisper tiny, int8 | `voice_whisper_model` (local dir) |
| TTS | Kokoro-82M (ONNX) | `models/tts/` |

Only the wake-word model is resident while idle. STT and TTS load on first use,
or stay warm when `voice_keep_warm` is true. The mic is muted while speaking and
for `voice_cooldown_s` afterwards, so the assistant cannot trigger its own wake
word. Any failure (no mic, missing model, empty transcript) is logged and the
loop returns to idle.

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
```
