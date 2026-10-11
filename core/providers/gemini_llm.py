"""Gemini reasoning (Google AI Studio).

Mirrors GroqLLM: declares the Zars action as a native function declaration,
streams the response, and validates with the same core/llm.py parser so
core/agent.py needs no changes.
"""
import json

from .base import LLMProvider, ProviderError
from .gemini_errors import translate


class GeminiLLM(LLMProvider):
    """Reasoning provider backed by Google AI Studio (Gemini).

    Does not subclass _SharedLLM: it does not reuse a core.llm.LLM transport,
    only that object's tool_schema() and _parse_response() helpers, so the
    prompt text and tool-pick validation stay identical across every provider.
    """

    name = "gemini (online)"

    def __init__(self, llm, api_key, model, timeout=30.0, client=None,
                 retry_after_max_s=2.0):
        self._llm = llm  # for tool_schema() / _parse_response()
        if not model:
            raise ProviderError("GEMINI_LLM_MODEL is not set")
        if not api_key:
            raise ProviderError("GEMINI_API_KEY is not set")
        self.model = model
        self.timeout = float(timeout)
        self.retry_after_max_s = float(retry_after_max_s)
        self._api_key = api_key
        self._client = client

    # --- interface bits ---

    def system_prompt(self):
        """Compact prompt, like Groq: the schema is declared natively."""
        return self._llm.system_prompt_compact()

    def _tool_schema(self):
        """Relaxed required list -- Gemini omits the empty fields too."""
        schema = json.loads(json.dumps(self._llm.tool_schema()))
        schema["required"] = ["tool", "thought"]
        return schema

    # --- transport ---

    def _get_client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(
                api_key=self._api_key,
                http_options={"timeout": int(self.timeout * 1000)},
            )
        return self._client

    def _tools(self):
        from google.genai import types
        return [types.Tool(function_declarations=[types.FunctionDeclaration(
            name="zars_action",
            description="Choose exactly ONE action for ZARS.",
            parameters=self._tool_schema(),
        )])]

    def _to_contents(self, messages):
        """[{role, content}] -> (system_instruction, [Content])."""
        from google.genai import types
        system = None
        contents = []
        for m in messages:
            role = m.get("role")
            text = m.get("content") or ""
            if role == "system":
                if system is None:
                    system = text
                continue
            contents.append(types.Content(
                role="model" if role == "assistant" else "user",
                parts=[types.Part.from_text(text=text)],
            ))
        return system, contents

    def _generate(self, messages):
        """One streamed call. Returns (content_text, tool_args_json)."""
        from google.genai import types

        client = self._get_client()
        system, contents = self._to_contents(messages)
        config = types.GenerateContentConfig(
            tools=self._tools(),
            temperature=self._config_temperature(),
            system_instruction=system,
        )
        text_parts, arg_frags = [], []
        try:
            stream = client.models.generate_content_stream(
                model=self.model, contents=contents, config=config)
            for chunk in stream:
                # Check function calls FIRST: reading .text on a chunk that
                # carries a function_call makes the SDK emit a warning on every
                # call, since it cannot render a non-text part as text.
                calls = getattr(chunk, "function_calls", None) or []
                if calls:
                    for fc in calls:
                        args = getattr(fc, "args", None)
                        if args:
                            arg_frags.append(json.dumps(dict(args)))
                        elif getattr(fc, "partial_args", None):
                            arg_frags.append(json.dumps(dict(fc.partial_args)))
                    continue
                text = getattr(chunk, "text", None)
                if text:
                    text_parts.append(text)
        except Exception as e:
            raise translate(e, "gemini") from e
        return "".join(text_parts), "".join(arg_frags)

    def _config_temperature(self):
        return getattr(self._llm.config, "temperature", 0.1)

    def _with_retry(self, fn):
        from .base import call_with_retry
        return call_with_retry(fn, self.retry_after_max_s)

    # --- interface ---

    def chat(self, messages, stream=True):
        """Yield raw text chunks (or the action JSON if it used a tool)."""
        content, args = self._with_retry(lambda: self._generate(messages))
        payload = args.strip() or content.strip()
        if not payload:
            return
        for i in range(0, len(payload), 64):
            yield payload[i:i + 64]

    def pick(self, messages):
        content, args = self._with_retry(lambda: self._generate(messages))
        raw = args.strip() or content.strip()
        if not raw:
            raise ProviderError("gemini returned an empty response")
        # Same validation as Ollama/Groq.
        try:
            return self._llm._parse_response(raw)
        except Exception as e:
            raise ProviderError(f"gemini response invalid: {e}") from e