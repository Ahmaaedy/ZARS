"""Provider factory.

Reads ZARS_MODE once at startup and returns the STT + LLM providers the
orchestrator should use. The orchestrator never sees the mode.

Fallback: when online fails and ONLINE_FALLBACK_TO_LOCAL is true, the local
provider is built lazily at that moment -- so a healthy online session never
pays the RAM cost of loading faster-whisper / Ollama.
"""
from rich.markup import escape

from .base import ProviderError

VALID_MODES = ("offline", "groq", "gemini")
# Backwards compatibility: ZARS_MODE=online was the original spelling for Groq.
MODE_ALIASES = {"online": "groq"}


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
    if raw in MODE_ALIASES:
        alias = raw
        raw = MODE_ALIASES[raw]
        return raw, (f"ZARS_MODE={alias!r} is deprecated; use {raw!r}. Continuing with {raw}.")
    if raw not in VALID_MODES:
        return "offline", (f"invalid ZARS_MODE={raw!r}; "
                           f"expected one of {VALID_MODES}. Using offline.")
    return raw, None


def _truthy(value, default=True):
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _offline_llm(agent):
    from .llm_providers import OllamaLLM
    return OllamaLLM(agent.llm)


def _build_groq_llm(config, agent):
    """None if GROQ_API_KEY is absent (caller degrades rather than fails)."""
    api_key = (getattr(config, "groq_api_key", "") or "").strip()
    if not api_key:
        return None
    from .llm_providers import GroqLLM
    return GroqLLM(
        agent.llm, api_key, getattr(config, "groq_llm_model", ""),
        timeout=float(getattr(config, "groq_timeout_seconds", 15) or 15),
        retry_after_max_s=float(getattr(config, "groq_retry_after_max_s", 2.0) or 0.0),
    )


def _build_gemini_llm(config, agent):
    """None if GEMINI_API_KEY is absent."""
    api_key = (getattr(config, "gemini_api_key", "") or "").strip()
    if not api_key:
        return None
    from .gemini_llm import GeminiLLM
    return GeminiLLM(
        agent.llm, api_key, getattr(config, "gemini_llm_model", ""),
        timeout=float(getattr(config, "gemini_timeout_seconds", 30) or 30),
        retry_after_max_s=float(getattr(config, "gemini_retry_after_max_s", 2.0) or 0.0),
    )


def build_llm(config, agent, log=None):
    """Return (llm_provider, note) for the current mode.

    Cross-cloud fallback: in groq mode Groq is primary and Gemini secondary;
    in gemini mode it is the reverse. Either way the local Ollama stack is the
    final fallback. Secondary clouds with no API key are skipped silently, and
    if neither cloud has a key we degrade to offline rather than fail.
    """
    log = log or (lambda m: None)
    mode, warning = resolve_mode(config)
    if warning:
        log(warning)

    if mode == "offline":
        return _offline_llm(agent), "offline: local ollama"

    if mode == "groq":
        primary, secondary, pname, sname = (
            _build_groq_llm(config, agent), _build_gemini_llm(config, agent),
            "groq chat", "gemini")
    else:  # gemini
        primary, secondary, pname, sname = (
            _build_gemini_llm(config, agent), _build_groq_llm(config, agent),
            "gemini", "groq chat")

    if primary is None:
        key = "GROQ_API_KEY" if mode == "groq" else "GEMINI_API_KEY"
        log(f"ZARS_MODE={mode} but {key} is empty -- running OFFLINE instead.")
        return _offline_llm(agent), f"offline ({mode} requested, no {key})"

    if not _truthy(getattr(config, "online_fallback_to_local", True)):
        return primary, f"{mode}: {pname} (no fallback)"

    chain = [primary]
    chain_desc = [pname]
    if secondary is not None:
        chain.append(secondary)
        chain_desc.append(sname)
    chain.append(_local_llm(agent))
    chain_desc.append("local ollama")

    if len(chain) == 2:  # only local left
        return FallbackLLM(primary, LazyLocal(lambda: _local_llm(agent)), log=log), \
            f"{mode}: {pname} -> local ollama"
    return ChainedLLM(chain, log=log), f"{mode}: " + " -> ".join(chain_desc)


class ChainedLLM:
    """Try each provider in order; the last one wins any failure."""

    def __init__(self, chain, log=None):
        self._chain = list(chain)
        self.name = " -> ".join(getattr(p, "name", "?") for p in self._chain)
        self._log = log or (lambda m: None)

    def system_prompt(self):
        return self._chain[0].system_prompt()

    def pick(self, messages):
        last = None
        for i, provider in enumerate(self._chain):
            try:
                return provider.pick(messages)
            except ProviderError as e:
                last = e
                if i < len(self._chain) - 1:
                    nxt = getattr(self._chain[i + 1], "name", "next")
                    # escape(): the log is a console, so "[zars]" would be read
                    # as a markup tag and dropped.
                    self._log(escape(f"[zars] {getattr(provider, 'name', 'provider')} "
                                     f"failed ({e}); trying {nxt}"))
        raise last

    def chat(self, messages, stream=True):
        last = None
        for i, provider in enumerate(self._chain):
            try:
                yield from provider.chat(messages, stream=stream)
                return
            except ProviderError as e:
                last = e
                if i < len(self._chain) - 1:
                    nxt = getattr(self._chain[i + 1], "name", "next")
                    self._log(escape(f"[zars] {getattr(provider, 'name', 'provider')} "
                                     f"failed ({e}); trying {nxt}"))
        raise last


def build_providers(config, registry, agent, log=None):
    """Return (stt_provider, llm_provider, mode_note).

    `agent` already owns a core.llm.LLM; we reuse it rather than constructing a
    second one, so system_prompt() and _parse_response() stay consistent.
    """
    log = log or (lambda m: None)
    llm, note = build_llm(config, agent, log=log)

    mode, _warning = resolve_mode(config)
    groq_key = (getattr(config, "groq_api_key", "") or "").strip()

    # STT is Groq whisper in BOTH online modes (gemini has no STT endpoint we
    # use); only reasoning moves to Gemini.
    if mode == "offline" or not groq_key:
        from .local_stt import build_local_stt
        if mode != "offline" and not groq_key:
            log("no GROQ_API_KEY -- using local whisper for STT")
        return build_local_stt(config), llm, note

    from .groq_stt import GroqSTT
    timeout = float(getattr(config, "groq_timeout_seconds", 15) or 15)
    retry_max = float(getattr(config, "groq_retry_after_max_s", 2.0) or 0.0)
    online_stt = GroqSTT(groq_key, getattr(config, "groq_stt_model", ""),
                         timeout=timeout,
                         language=getattr(config, "groq_stt_language", "en"),
                         retry_after_max_s=retry_max)
    local_stt = LazyLocal(lambda: _local_stt(config))

    if _truthy(getattr(config, "online_fallback_to_local", True)):
        return FallbackSTT(online_stt, local_stt, log=log), llm, \
            f"{mode}: groq whisper (+ local fallback)"
    return online_stt, llm, f"{mode}: groq whisper (no fallback)"


def _local_stt(config):
    from .local_stt import build_local_stt
    return build_local_stt(config)


def _local_llm(agent):
    from .llm_providers import OllamaLLM
    return OllamaLLM(agent.llm)