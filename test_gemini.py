"""Gemini provider tests: model discovery, streaming, and fallback behaviour.

    python test_gemini.py models    list models your API key can actually reach
    python test_gemini.py env       resolved mode + config (never prints the key)
    python test_gemini.py llm       stream one GeminiLLM reply as a tool pick
    python test_gemini.py failures  bad key / no network / fallback chain
    python test_gemini.py all

STT is not tested here: it stays on Groq in every online mode.
See test_groq.py for the STT stage.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.config import Config
from core.providers.base import ProviderError
from core.providers.factory import resolve_mode


def _hr(t):
    print(f"\n{'=' * 62}\n{t}\n{'=' * 62}")


def _cfg():
    return Config()


def _have_key(cfg):
    key = (getattr(cfg, "gemini_api_key", "") or "").strip()
    if not key:
        print("GEMINI_API_KEY is empty -- add it to .env (see .env.example).")
        print("Skipping network tests.")
        return False
    return True


def test_models(cfg):
    """Discovery: what can this key actually call? Use this to pick the ID."""
    _hr("MODEL DISCOVERY")
    if not _have_key(cfg):
        return
    from google import genai
    try:
        client = genai.Client(api_key=cfg.gemini_api_key)
    except Exception as e:
        print(f"client init failed: {type(e).__name__}: {e}")
        return
    try:
        listing = client.models.list()
    except Exception as e:
        print(f"list failed: {type(e).__name__}: {e}")
        return
    ids = []
    for m in getattr(listing, "models", []) or []:
        mid = getattr(m, "name", "") or ""
        ids.append(mid)
    print(f"{len(ids)} models visible to this key:")
    for mid in sorted(ids):
        print("  ", mid)
    print()
    print("configured GEMINI_LLM_MODEL:", cfg.gemini_llm_model)
    flash_lite = [i for i in ids if "flash-lite" in i.lower()]
    if flash_lite:
        print("flash-lite candidates:")
        for i in sorted(flash_lite):
            marker = "  <- configured" if cfg.gemini_llm_model in i else ""
            print("  ", i, marker)
    else:
        print("no flash-lite model visible -- check access tier")


def test_env(cfg):
    _hr("ENV / CONFIG (key never printed)")
    mode, warning = resolve_mode(cfg)
    print(f"ZARS_MODE (resolved) : {mode}")
    if warning:
        print(f"  warning: {warning}")
    g = getattr(cfg, "gemini_api_key", "") or ""
    k = getattr(cfg, "groq_api_key", "") or ""
    print(f"GEMINI_API_KEY       : {'set (len=%d, hidden)' % len(g) if g.strip() else 'NOT SET'}")
    print(f"GROQ_API_KEY         : {'set (len=%d, hidden)' % len(k) if k.strip() else 'NOT SET'}")
    print(f"GEMINI_LLM_MODEL     : {cfg.gemini_llm_model}")
    print(f"GEMINI_TIMEOUT       : {cfg.gemini_timeout_seconds}")
    print(f"GROQ_STT_MODEL       : {cfg.groq_stt_model}  (STT stays Groq)")
    print(f"FALLBACK_TO_LOCAL    : {cfg.online_fallback_to_local}")


def _llm(cfg):
    from core.llm import LLM
    from core.providers.gemini_llm import GeminiLLM
    from core.registry import load_registry
    return GeminiLLM(LLM(cfg, load_registry()), cfg.gemini_api_key,
                     cfg.gemini_llm_model,
                     timeout=cfg.gemini_timeout_seconds,
                     retry_after_max_s=cfg.gemini_retry_after_max_s)


def test_llm(cfg):
    _hr("GeminiLLM")
    if not _have_key(cfg):
        return
    from core.session import Session
    g = _llm(cfg)
    print(f"model : {g.model}")
    print(f"prompt: {len(g.system_prompt())} chars (compact)")
    for p in ["what is 2+2", "what time is it", "hello"]:
        s = Session(g.system_prompt())
        s.add("user", p)
        try:
            t0 = time.monotonic()
            pick = g.pick(s.messages)
            print("  OK   %-16s %5.2fs tool=%-6s resp=%r"
                  % (p[:15], time.monotonic() - t0, pick.get("tool"),
                     str(pick.get("response"))[:34]))
        except Exception as e:
            print("  FAIL %-16s %s: %s" % (p[:15], type(e).__name__, str(e)[:60]))
        time.sleep(2)


def test_failures(cfg):
    _hr("FAILURE HANDLING / FALLBACK CHAIN")
    from core.providers.gemini_errors import translate
    from core.providers.gemini_llm import GeminiLLM
    from core.llm import LLM
    from core.registry import load_registry
    from google.genai import errors as ge

    print("[1] error category mapping")
    class FakeErr(ge.APIError):
        def __init__(self, code, msg="synthetic"):
            super().__init__(msg, response_json={"error": {"code": code}})
            self.code = code
    for code, want in ((401, "AuthError"), (403, "AuthError"), (429, "RateLimitError"),
                       (500, "ServerError"), (400, "ProviderError")):
        got = type(translate(FakeErr(code))).__name__
        print("    %-4s -> %-16s %s" % (code, got, "OK" if got == want else "EXPECTED " + want))

    print("[2] bad key -> AuthError")
    g_bad = GeminiLLM(LLM(cfg, load_registry()), "invalid-key-for-test",
                      cfg.gemini_llm_model, timeout=15)
    from core.session import Session
    s = Session(g_bad.system_prompt()); s.add("user", "hi")
    try:
        g_bad.pick(s.messages)
        print("    no error raised (unexpected)")
    except ProviderError as e:
        print("    ->", type(e).__name__, ":", str(e)[:80])

    print("[3] no network -> OfflineError")
    g_off = GeminiLLM(LLM(cfg, load_registry()), cfg.gemini_api_key or "x",
                      cfg.gemini_llm_model, timeout=3, retry_after_max_s=0)
    g_off._client = _DeadClient()
    try:
        g_off.pick(s.messages)
        print("    no error raised (unexpected)")
    except ProviderError as e:
        print("    ->", type(e).__name__, ":", str(e)[:80])

    print("[4] factory chain: gemini mode, no keys -> offline")
    c = Config(); c.zars_mode = "gemini"; c.gemini_api_key = ""; c.groq_api_key = ""
    from core.providers.factory import build_llm
    ag = type("A", (), {})(); ag.llm = LLM(c, load_registry())
    llm, note = build_llm(c, ag)
    print("    note:", note, "| provider:", llm.name)

    print("[5] factory chain: gemini mode, both keys -> 3-deep chain")
    c2 = Config(); c2.zars_mode = "gemini"
    c2.gemini_api_key = "fake-gemini-key"
    c2.groq_api_key = "fake-groq-key"
    llm2, note2 = build_llm(c2, ag)
    print("    note:", note2)
    print("    chain:", llm2.name)
    expect = ["gemini (online)", "groq chat (online)", "ollama (local)"]
    print("    order correct:", llm2.name == " -> ".join(expect))


class _DeadClient:
    """Client stub whose streaming call raises a connection error."""
    class models:
        @staticmethod
        def generate_content_stream(**kw):
            raise ConnectionError("failed to establish a connection")


def test_all(cfg):
    test_env(cfg)
    test_models(cfg)
    test_llm(cfg)
    test_failures(cfg)


def main():
    ap = argparse.ArgumentParser(description="Gemini provider tests")
    ap.add_argument("stage",
                    choices=["models", "env", "llm", "failures", "all"],
                    default="env", nargs="?")
    args = ap.parse_args()
    cfg = _cfg()
    {"env": test_env, "models": test_models, "llm": test_llm,
     "failures": test_failures, "all": test_all}[args.stage](cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())