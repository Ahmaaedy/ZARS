"""Ollama reasoning (local) and Groq reasoning (cloud).

Both reuse the existing core/llm.py LLM object for its system prompt and its
response parser, so the prompt text and the tool-pick validation stay in exactly
one place regardless of mode. Only the transport differs.
"""
from .base import LLMProvider, MalformedOutputError, ProviderError


class _SharedLLM(LLMProvider):
    """Common behaviour backed by a core.llm.LLM instance."""

    def __init__(self, llm):
        self._llm = llm

    def system_prompt(self):
        return self._llm.system_prompt()

    @property
    def tool_schema(self):
        return self._llm.tool_schema()


class OllamaLLM(_SharedLLM):
    name = "ollama (local)"

    def pick(self, messages):
        try:
            return self._llm.pick(messages)
        except Exception as e:
            raise ProviderError(f"ollama failed: {e}") from e

    def chat(self, messages, stream=True):
        text = self._llm._pick_ollama(messages)
        if text:
            yield text


class GroqLLM(_SharedLLM):
    name = "groq chat (online)"

    def __init__(self, llm, api_key, model, timeout=15.0, client=None,
                 retry_after_max_s=2.0):
        super().__init__(llm)  # sets self._llm (system prompt + response parser)
        if not model:
            raise ProviderError("GROQ_LLM_MODEL is not set")
        self.model = model
        self.timeout = float(timeout)
        self.retry_after_max_s = float(retry_after_max_s)
        self._api_key = api_key
        self._client = client

    def system_prompt(self):
        """Compact prompt: the tool schema is already sent as a function
        definition, so the long trigger/example prose is redundant and would
        burn the 8k TPM budget on every call."""
        return self._llm.system_prompt_compact()

    def _get_client(self):
        if self._client is None:
            import groq
            self._client = groq.Groq(
                api_key=self._api_key,
                timeout=self.timeout,
                max_retries=0,  # no retry loops
            )
        return self._client

    def _tools(self):
        """Expose the Zars action as a real function tool.

        gpt-oss models are tool-calling-native: with a tool-heavy system prompt
        they emit a native tool call even when we only asked for JSON, which
        Groq rejects ("Tool choice is none, but model called a tool"). Declaring
        the schema as a function makes that call *valid* and additionally
        constrains `tool` to the registry enum at the provider level, which
        Ollama's constrained decoding used to do for us.

        Only `tool` and `thought` are required: models routinely omit the empty
        ones (args/command/response), and demanding them makes Groq reject the
        call with "Tool call validation failed". _parse_response already
        defaults `args` to {} and the agent reads the rest with .get().
        """
        import copy
        schema = copy.deepcopy(self._llm.tool_schema())
        schema["required"] = ["tool", "thought"]
        return [{
            "type": "function",
            "function": {
                "name": "zars_action",
                "description": "Choose exactly ONE action for ZARS.",
                "parameters": schema,
            },
        }]

    def _with_retry(self, fn):
        from .groq_errors import call_with_retry
        return call_with_retry(fn, self.retry_after_max_s)

    def _stream(self, messages):
        """Stream once. Returns (content_text, tool_args_json, tool_name).

        Tries native tool calling first. If the model produces output the
        provider cannot parse, falls back ONCE to plain JSON mode, which is far
        more constrained and so much less likely to fail the same way. Each path
        gets one bounded Retry-After wait; auth/rate-limit/offline errors that
        outlast it propagate so the factory can decide on local fallback.
        """
        try:
            return self._with_retry(lambda: self._stream_tools(messages))
        except ProviderError as e:
            # Key off the error class, not the message. Groq words this same
            # problem several ways -- "Parsing failed...", "Tool choice is
            # none...", "Tool call validation failed" -- and the parse-failure
            # wording contains neither "tool call" nor "validation failed", so
            # the old substring test let the one recovery path below go unused.
            # The substring check stays as a net for wording we have not seen.
            if not isinstance(e, MalformedOutputError) and "tool call" not in str(e).lower():
                raise
            return self._with_retry(lambda: self._stream_json(messages))

    def _stream_json(self, messages):
        from .groq_errors import translate

        client = self._get_client()
        content = []
        kwargs = {
            "model": self.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            **self._sampling(),
        }
        try:
            for chunk in client.chat.completions.create(stream=True, **kwargs):
                if not chunk.choices:
                    continue
                piece = getattr(chunk.choices[0].delta, "content", None)
                if piece:
                    content.append(piece)
        except Exception as e:
            raise translate(e, "groq llm") from e
        return "".join(content), "", None

    def _stream_tools(self, messages):
        from .groq_errors import translate

        client = self._get_client()
        content, arg_frags, tool_name = [], {}, None
        kwargs = {
            "model": self.model,
            "messages": messages,
            "tools": self._tools(),
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            **self._sampling(),
        }
        try:
            for chunk in client.chat.completions.create(stream=True, **kwargs):
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if getattr(delta, "content", None):
                    content.append(delta.content)
                for tc in (getattr(delta, "tool_calls", None) or []):
                    fn = getattr(tc, "function", None)
                    if fn is None:
                        continue
                    if getattr(fn, "name", None):
                        tool_name = fn.name
                    frag = getattr(fn, "arguments", None)
                    if frag:
                        idx = getattr(tc, "index", 0) or 0
                        arg_frags[idx] = arg_frags.get(idx, "") + frag
        except Exception as e:
            raise translate(e, "groq llm") from e
        return "".join(content), "".join(v for _, v in sorted(arg_frags.items())), tool_name

    def chat(self, messages, stream=True):
        """Yield raw text chunks in the same shape OllamaLLM yields.

        If the model answered via a tool call, the accumulated action JSON is
        yielded once, since there is no free prose to stream.
        """
        content, args_json, _ = self._stream(messages)
        if content:
            for i in range(0, len(content), 64):
                yield content[i:i + 64]
        elif args_json:
            yield args_json

    def _config_temperature(self):
        """Groq's own sampling temperature, not the shared config.json one.

        config.json ships temperature 1 because that suits the local model.
        Groq documents gpt-oss as best at 0.5-0.7 (server default 0.6), and
        sampling at 1.0 makes malformed tool-call syntax more likely, so
        groq_temperature wins whenever it is set.
        """
        override = getattr(self._llm.config, "groq_temperature", None)
        if override is not None:
            try:
                return float(override)
            except (TypeError, ValueError):
                pass
        return getattr(self._llm.config, "temperature", 0.1)

    def _sampling(self):
        """Temperature and token/effort ceilings shared by both stream paths.

        max_completion_tokens matters more than it looks: Groq's server default
        is 1024 for the whole response, and a reasoning model spends most of
        that before it starts the tool call. Too small a ceiling truncates the
        arguments mid-JSON, which is what produces "Parsing failed".

        reasoning_effort is sent only for gpt-oss. Groq supports it on
        gpt-oss-20b/120b specifically, and other models reject unknown request
        parameters with a 400.
        """
        out = {"temperature": self._config_temperature()}

        limit = getattr(self._llm.config, "groq_max_completion_tokens", None)
        try:
            limit = int(limit) if limit is not None else 0
        except (TypeError, ValueError):
            limit = 0
        if limit > 0:
            out["max_completion_tokens"] = limit

        effort = str(getattr(self._llm.config, "groq_reasoning_effort", "") or "").strip().lower()
        if effort and "gpt-oss" in self.model.lower():
            out["reasoning_effort"] = effort

        return out

    def pick(self, messages):
        """Stream, normalise to the Zars pick shape, validate with the shared
        parser. Identical output to the Ollama path, so agent.py is unchanged.
        """
        content, args_json, _tool_name = self._stream(messages)
        raw = args_json.strip() or content.strip()
        if not raw:
            raise ProviderError("groq returned an empty response")
        # Reuse the exact same validation the Ollama path uses.
        try:
            return self._llm._parse_response(raw)
        except Exception as e:
            raise ProviderError(f"groq response invalid: {e}") from e