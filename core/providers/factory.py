"""Provider factory.

Reads ZARS_MODE once at startup and returns the STT + LLM providers the
orchestrator should use. The orchestrator never sees the mode.

Fallback: when online fails and ONLINE_FALLBACK_TO_LOCAL is true, the local
provider is built lazily at that moment -- so a healthy online session never
pays the RAM cost of loading faster-whisper / Ollama.
"""
from .base import ProviderError

VALID_MODES = ("offline", "online")


class LazyLocal:
    """Builds the local provider on first use, then caches it."""

    def __init__(self, builder):
        self._builder = builder
        self._obj = None

    def get(self):
        if self._obj is None:
            self._obj = self._builder()
        return self._obj


class FallbackSTT:
    """Online STT with an optional lazy local fallback."""

    name = "groq whisper (online, local fallback)"

    def __init__(self, online, local_getter, log=None):
        self._online = online
        self._local = local_getter
        self._log = log or (lambda m: None)

    def transcribe(self, samples, sample_rate=16000):
        try:
            return self._online.transcribe(samples, sample_rate)
        except ProviderError as e:
            self._log(f"online STT failed ({e}); falling back to local whisper")
            local = self._local.get()
            return local.transcribe(samples, sample_rate)


class FallbackLLM:
    """Online LLM with an optional lazy local fallback."""

    name = "groq chat (online, local fallback)"

    def __init__(self, online, local_getter, log=None):
        self._online = online
        self._local = local_getter
        self._log = log or (lambda m: None)

    def system_prompt(self):
        return self._online.system_prompt()

    def pick(self, messages):
        try:
            return self._online.pick(messages)
        except ProviderError as e:
            self._log(f"online LLM failed ({e}); falling back to local ollama")
            local = self._local.get()
            return local.pick(messages)

    def chat(self, messages, stream=True):
        try:
            yield from self._online.chat(messages, stream=stream)
        except ProviderError as e:
            self._log(f"online LLM failed ({e}); falling back to local ollama")
            local = self._local.get()
            yield from local.chat(messages, stream=stream)


def resolve_mode(config):
    """Return (mode, warning). Invalid values fall back to offline."""
    raw = str(getattr(config, "zars_mode", "offline") or "offline").strip().lower()
    if raw not in VALID_MODES:
        return "offline", (f"invalid ZARS_MODE={raw!r}; "
                           f"expected one of {VALID_MODES}. Using offline.")
    return raw, None


def _truthy(value, default=True):
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def build_providers(config, registry, agent, log=None):
    """Return (stt_provider, llm_provider, mode_note).

    `agent` already owns a core.llm.LLM; we reuse it rather than constructing a
    second one, so system_prompt() and _parse_response() stay consistent.
    """
    log = log or (lambda m: None)
    mode, warning = resolve_mode(config)
    if warning:
        log(warning)
    if mode == "offline":
        from .llm_providers import OllamaLLM
        from .local_stt import build_local_stt
        return build_local_stt(config), OllamaLLM(agent.llm), "offline: local whisper + local ollama"

    api_key = getattr(config, "groq_api_key", "") or ""
    if not api_key.strip():
        log("ZARS_MODE=online but GROQ_API_KEY is empty -- running OFFLINE instead.")
        from .llm_providers import OllamaLLM
        from .local_stt import build_local_stt
        return build_local_stt(config), OllamaLLM(agent.llm), "offline (online requested, no API key)"

    from .groq_stt import GroqSTT
    from .llm_providers import GroqLLM

    timeout = float(getattr(config, "groq_timeout_seconds", 15) or 15)
    stt_model = getattr(config, "groq_stt_model", "")
    llm_model = getattr(config, "groq_llm_model", "")
    lang = getattr(config, "groq_stt_language", "en")

    online_stt = GroqSTT(api_key, stt_model, timeout=timeout, language=lang)
    online_llm = GroqLLM(agent.llm, api_key, llm_model, timeout=timeout)

    # Lazy local providers for fallback -- not built until actually needed.
    local_stt = LazyLocal(lambda: _local_stt(config))
    local_llm = LazyLocal(lambda: _local_llm(agent))

    if _truthy(getattr(config, "online_fallback_to_local", True)):
        stt = FallbackSTT(online_stt, local_stt, log=log)
        llm = FallbackLLM(online_llm, local_llm, log=log)
        note = "online: groq whisper + groq chat (falls back to local)"
    else:
        stt, llm = online_stt, online_llm
        note = "online: groq whisper + groq chat (no fallback)"
    return stt, llm, note


def _local_stt(config):
    from .local_stt import build_local_stt
    return build_local_stt(config)


def _local_llm(agent):
    from .llm_providers import OllamaLLM
    return OllamaLLM(agent.llm)