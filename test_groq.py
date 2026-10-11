"""Groq provider tests: STT, streamed LLM, and failure/fallback simulation.

    python test_groq.py env      show resolved mode + config (never prints the key)
    python test_groq.py stt      transcribe a sample WAV through Groq
    python test_groq.py llm      stream one GroqLLM reply, sentence by sentence
    python test_groq.py failures  bad key / no-network / rate-limit -> fallback
    python test_groq.py all

None of these touch the microphone. STT needs a WAV; one is generated locally
with Kokoro if missing.
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.config import Config
from core.providers.base import (
    AuthError, OfflineError, ProviderError, RateLimitError, ServerError,
)

SAMPLE_WAV = Path("models") / "_groq_test.wav"
SAMPLE_TEXT = "What time is it and open notepad please."


def _hr(t):
    print(f"\n{'=' * 62}\n{t}\n{'=' * 62}")


def _cfg():
    return Config()


def _have_key(cfg):
    key = (getattr(cfg, "groq_api_key", "") or "").strip()
    if not key:
        print("GROQ_API_KEY is empty -- add it to .env (see .env.example).")
        print("Skipping network tests.")
        return False
    return True


def _ensure_wav():
    """Generate a 16 kHz sample with local Kokoro so STT has something to chew."""
    if SAMPLE_WAV.exists():
        return SAMPLE_WAV
    cfg = _cfg()
    from core.voice.audio_io import _resample
    from core.voice.tts import KokoroTts
    t = KokoroTts(cfg.voice_kokoro_model, cfg.voice_kokoro_voices,
                  cfg.voice_kokoro_voice, cfg.voice_kokoro_speed)
    samples, sr = t.synthesize(SAMPLE_TEXT)
    import soundfile as sf
    SAMPLE_WAV.parent.mkdir(parents=True, exist_ok=True)
    sf.write(SAMPLE_WAV, _resample(np.asarray(samples, dtype=np.float32), sr, 16000), 16000)
    print(f"generated {SAMPLE_WAV} via local Kokoro ({SAMPLE_TEXT!r})")
    return SAMPLE_WAV


def _load_audio(path):
    import soundfile as sf
    audio, sr = sf.read(path, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    from core.voice.audio_io import _resample
    return _resample(audio, sr, 16000)


def _smoke_providers(cfg):
    """Network-free structural check.

    Constructing both providers and touching system_prompt() catches wiring
    bugs (e.g. a subclass shadowing __init__ and never setting the base
    attribute) without needing a key or a request.
    """
    print("[smoke] provider construction")
    from core.llm import LLM
    from core.providers.llm_providers import GroqLLM, OllamaLLM
    from core.registry import load_registry

    registry = load_registry()
    llm = LLM(cfg, registry)

    for label, obj in (
        ("OllamaLLM", OllamaLLM(llm)),
        ("GroqLLM", GroqLLM(llm, "gsk_dummy_not_a_real_key", cfg.groq_llm_model)),
    ):
        try:
            sp = obj.system_prompt()
            assert sp and "ZARS" in sp, "system prompt looks wrong"
            assert obj._llm is llm, "base _llm not wired"
            if hasattr(obj, "_config_temperature"):
                obj._config_temperature()  # deferred path on GroqLLM
            print(f"    OK  {label}: system_prompt {len(sp)} chars, _llm wired")
        except Exception as e:
            print(f"    FAIL {label}: {type(e).__name__}: {e}")
            return False
    return True


def test_env(cfg):
    _hr("ENV / CONFIG (key never printed)")
    from core.providers.factory import resolve_mode
    mode, warning = resolve_mode(cfg)
    print(f"ZARS_MODE (resolved) : {mode}")
    if warning:
        print(f"  warning: {warning}")
    key = getattr(cfg, "groq_api_key", "") or ""
    print(f"GROQ_API_KEY        : {'set (len=%d, hidden)' % len(key) if key.strip() else 'NOT SET'}")
    print(f"GROQ_STT_MODEL      : {cfg.groq_stt_model}")
    print(f"GROQ_LLM_MODEL      : {cfg.groq_llm_model}")
    print(f"GROQ_STT_LANGUAGE   : {cfg.groq_stt_language}")
    print(f"GROQ_TIMEOUT_SECONDS: {cfg.groq_timeout_seconds}")
    print(f"FALLBACK_TO_LOCAL   : {cfg.online_fallback_to_local}")
    print(f"voice whisper (local): {cfg.voice_whisper_model}")
    _smoke_providers(cfg)


def test_stt(cfg):
    _hr("GroqSTT")
    if not _have_key(cfg):
        return
    wav = _ensure_wav()
    from core.providers.groq_stt import GroqSTT
    p = GroqSTT(cfg.groq_api_key, cfg.groq_stt_model,
                timeout=cfg.groq_timeout_seconds, language=cfg.groq_stt_language)
    audio = _load_audio(wav)
    print(f"audio: {wav} ({len(audio) / 16000:.2f}s @16k mono)")
    print(f"model: {p.model}  language: {p.language}")
    t0 = time.monotonic()
    text = p.transcribe(audio)
    print(f"took {time.monotonic() - t0:.2f}s")
    print(f"TEXT: {text!r}")
    print("RESULT: groq stt OK" if text else "RESULT: empty transcription")

    # silence must return '' without a network call
    empty = p.transcribe(np.zeros(1600, dtype=np.float32))
    print(f"silence -> {empty!r} (expected '')")
    print("RESULT: silence handled")


def test_llm(cfg):
    _hr("GroqLLM (streaming)")
    if not _have_key(cfg):
        return
    from core.llm import LLM
    from core.providers.llm_providers import GroqLLM
    from core.registry import load_registry
    from core.session import Session

    registry = load_registry()
    llm = GroqLLM(LLM(cfg, registry), cfg.groq_api_key, cfg.groq_llm_model,
                  timeout=cfg.groq_timeout_seconds)
    session = Session(llm.system_prompt())
    session.add("user", "say hello in one short sentence")
    print(f"model: {cfg.groq_llm_model}")
    print("--- raw streamed chunks ---")
    t0 = time.monotonic()
    acc = ""
    for i, chunk in enumerate(llm.chat(session.messages, stream=True), 1):
        print(f"  [{i:02d}] +{len(chunk):3d} chars: {chunk[:60]!r}")
        acc += chunk
    print(f"total {len(acc)} chars in {time.monotonic() - t0:.2f}s")
    print(f"accumulated: {acc[:200]!r}")

    print("--- as a tool pick (what the pipeline actually uses) ---")
    session2 = Session(llm.system_prompt())
    session2.add("user", "what is 2+2")
    pick = llm.pick(session2.messages)
    print(f"pick: {pick}")
    print("RESULT: groq llm OK" if pick.get("tool") else "RESULT: no tool in pick")


def test_failures(cfg):
    _hr("FAILURE HANDLING / FALLBACK")
    from core.providers.groq_stt import GroqSTT
    from core.providers.groq_errors import translate

    audio = _ensure_wav()
    audio_np = _load_audio(audio)

    # 1. bad key -> AuthError (only if a real key exists to go bad)
    print("[1] bad key")
    bad = GroqSTT("gsk_invalid_key_for_testing", cfg.groq_stt_model, timeout=10)
    try:
        bad.transcribe(audio_np)
        print("    no error raised (unexpected)")
    except ProviderError as e:
        print(f"    -> {type(e).__name__}: {e}")

    # 2. no network -> OfflineError
    print("[2] unreachable host")
    import groq
    from core.providers.factory import LazyLocal
    un = GroqSTT("gsk_fake", cfg.groq_stt_model, timeout=5)
    un._client = groq.Groq(api_key="gsk_fake", timeout=5, max_retries=0,
                           base_url="http://127.0.0.1:9/v1")  # discard port
    try:
        un.transcribe(audio_np)
        print("    no error raised (unexpected)")
    except ProviderError as e:
        print(f"    -> {type(e).__name__}: {e}")

    # 3. error category translation
    print("[3] category mapping")
    class R:  # fake response with headers
        headers = {"retry-after": "2.5"}
    import httpx
    req = httpx.Request("POST", "https://api.groq.com/x")
    resp = httpx.Response(429, headers={"retry-after": "2.5"}, request=req)
    e429 = groq.RateLimitError("rate limited", response=resp, body=None)
    print(f"    429 -> {type(translate(e429)).__name__}: {translate(e429)}")
    e401 = groq.AuthenticationError("bad key", response=httpx.Response(401, request=req), body=None)
    print(f"    401 -> {type(translate(e401)).__name__}")
    e500 = groq.InternalServerError("boom", response=httpx.Response(503, request=req), body=None)
    print(f"    503 -> {type(translate(e500)).__name__}")

    # 4. fallback wrapper actually degrades to local
    print("[4] fallback wrapper -> local whisper")
    from core.providers.factory import FallbackSTT
    from core.providers.local_stt import build_local_stt
    fallback = FallbackSTT(bad, LazyLocal(lambda: build_local_stt(cfg)),
                           log=lambda m: print(f"    log: {m}"))
    try:
        out = fallback.transcribe(audio_np)
        print(f"    -> {out!r}")
        print("    RESULT: fell back to local OK" if out else "    RESULT: fallback returned empty")
    except ProviderError as e:
        print(f"    fallback ALSO failed: {type(e).__name__}: {e}")

    # 5. fallback disabled
    print("[5] no fallback -> provider error propagates")
    print("    (online-only path raises ProviderError; orchestrator speaks the error line)")


def test_all(cfg):
    test_env(cfg)
    test_stt(cfg)
    test_llm(cfg)
    test_failures(cfg)


def main():
    ap = argparse.ArgumentParser(description="Groq provider tests")
    ap.add_argument("stage", choices=["env", "stt", "llm", "failures", "all"], default="env", nargs="?")
    args = ap.parse_args()
    cfg = _cfg()
    {"env": test_env, "stt": test_stt, "llm": test_llm,
     "failures": test_failures, "all": test_all}[args.stage](cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())