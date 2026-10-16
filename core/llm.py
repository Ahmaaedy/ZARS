import json
from datetime import datetime

TOOL_IDS_EXTRA = ["shell", "done", "ask"]


class LLMError(Exception):
    pass


class LLM:
    def __init__(self, config, registry, log=None):
        self.config = config
        self.registry = registry
        self.provider = getattr(config, "provider", "ollama")
        self._log = log

        if self.provider == "ollama":
            from ollama import Client
            self.client = Client(host=config.host)
        else:
            raise LLMError(
                f"unknown provider: {self.provider!r} "
                "(core/llm.py serves Ollama only; use ZARS_MODE for Gemini/Groq)"
            )

    def _autostart(self):
        from .ollama_serve import ensure_serving
        ensure_serving(self.config, log=self._log)

    def _registry_lines(self):
        lines = []
        for entry in self.registry.entries.values():
            ex = f' example: "{entry.examples[0]}"' if entry.examples else ""
            triggers = ", ".join(f'"{t}"' for t in entry.triggers) if entry.triggers else "none"
            lines.append(f'- {entry.id} — {entry.desc}. triggers: [{triggers}]. params: {entry.param_line()}.{ex}')
        return "\n".join(lines)

    def system_prompt(self):
        return (
            "You are ZARS, an on-device terminal assistant for Windows (PowerShell).\n"
            f"Current date and time: {datetime.now().strftime('%A, %B %d, %Y %I:%M %p')}\n"
            "You complete the user's request by choosing ONE action per turn, as JSON.\n\n"
            "Available tools:\n"
            f"{self._registry_lines()}\n\n"
            "Special tools:\n"
            '- shell — run any PowerShell command when no tool fits; set "command" to the command\n'
            '- done — finish; "response" is the final answer for the user\n'
            '- ask — ask the user a clarifying question; "response" is the question\n\n'
            "Rules:\n"
            "- Prefer registry tools over shell. Never invent tool ids.\n"
            "- args keys must exactly match the tool's params.\n"
            '- When tool is "shell", the PowerShell command MUST go in "command" (never in response).\n'
            '- When tool is "done" or "ask", put your text in "response".\n'
            "- One action per turn; you will receive its result, then choose again.\n"
            "- Keep shell commands simple and non-destructive.\n"
            "- For questions you can answer directly (math, facts, explanations), use done immediately.\n"
            "- Never use tools for questions you can answer yourself.\n"
            "- For casual greetings or conversation (hello, hey, what's up, how are you, etc.), use done to respond naturally and briefly.\n"
            "- You may receive fuzzy match hints. If a fuzzy match is provided and seems correct, use that tool. If ambiguous, pick the best match or ask for clarification.\n"
            "- If the user wants to create, make, build, or generate a new skill, use the 'create_skill' tool with a description of what the skill should do.\n"
            "- If no tool fits, use done to explain what tools are available. Never guess or hallucinate shell commands.\n\n"
            "Examples:\n"
            'User: what is 2+2\n'
            '{"thought": "simple math", "tool": "done", "args": {}, "command": "", "response": "4"}\n\n'
            'User: hey\n'
            '{"thought": "casual greeting", "tool": "done", "args": {}, "command": "", "response": "Hey! What can I help you with?"}\n\n'
            'User: what is 2+2\n'
            '{"thought": "simple math", "tool": "done", "args": {}, "command": "", "response": "4"}\n\n'
            'User: search for how to bake a cake\n'
            '{"thought": "need web search", "tool": "web_search", "args": {"query": "how to bake a cake"}, "command": "", "response": ""}\n\n'
            'User: take a screenshot\n'
            '{"thought": "need screenshot", "tool": "take_screenshot", "args": {}, "command": "", "response": ""}\n\n'
            'User: weather in london and tell me the best time to visit\n'
            '{"thought": "first get weather data", "tool": "check_weather", "args": {"location": "london"}, "command": "", "response": ""}\n\n'
            'User: I want to watch a video about cats\n'
            '{"thought": "search for cat videos first", "tool": "web_search", "args": {"query": "cat videos"}, "command": "", "response": ""}\n'
            '[tool result with a YouTube link]\n'
            '{"thought": "open the video link", "tool": "open_url", "args": {"url": "https://youtube.com/watch?v=..."}, "command": "", "response": ""}\n\n'
            "Respond with JSON only."
        )

    def _registry_lines_compact(self):
        """Trimmed tool list: id(param_names): desc.

        Drops triggers and examples, which cost ~2000 chars across 24 entries.
        Safe for providers that constrain output natively (Groq sends the full
        schema as a function definition), since the prompt prose was only ever a
        belt-and-braces duplicate of that constraint.
        """
        lines = []
        for entry in self.registry.entries.values():
            params = ",".join(p["name"] for p in entry.params)
            lines.append(f"- {entry.id}({params}): {entry.desc}")
        return "\n".join(lines)

    def system_prompt_compact(self):
        """Same prompt as system_prompt() with the tool list compacted.

        Used only by the Groq provider to stay inside the TPM rate limit.
        Ollama keeps the full prompt because its constrained decoding still
        relies on that prose to steer JSON tool-picks.
        """
        return (
            "You are ZARS, an on-device terminal assistant for Windows (PowerShell).\n"
            f"Current date and time: {datetime.now().strftime('%A, %B %d, %Y %I:%M %p')}\n"
            "You complete the user's request by choosing ONE action per turn, as JSON.\n\n"
            "Available tools:\n"
            f"{self._registry_lines_compact()}\n\n"
            "Special tools:\n"
            '- shell — run any PowerShell command when no tool fits; set "command" to the command\n'
            '- done — finish; "response" is the final answer for the user\n'
            '- ask — ask the user a clarifying question; "response" is the question\n\n'
            "Rules:\n"
            "- Prefer registry tools over shell. Never invent tool ids.\n"
            "- args keys must exactly match the tool's params.\n"
            '- When tool is "shell", the PowerShell command MUST go in "command" (never in response).\n'
            '- When tool is "done" or "ask", put your text in "response".\n'
            "- One action per turn; you will receive its result, then choose again.\n"
            "- Keep shell commands simple and non-destructive.\n"
            "- For questions you can answer directly (math, facts, explanations), use done immediately.\n"
            "- Never use tools for questions you can answer yourself.\n"
            "- For casual greetings or conversation, use done to respond naturally and briefly.\n"
            "- If the user wants to create, make, build, or generate a new skill, use the 'create_skill' tool.\n"
            "- If no tool fits, use done to explain what tools are available. Never guess or hallucinate shell commands.\n\n"
            "Respond with JSON only."
        )

    def tool_schema(self):
        return {
            "type": "object",
            "properties": {
                "thought": {"type": "string"},
                "tool": {"type": "string", "enum": list(self.registry.entries.keys()) + TOOL_IDS_EXTRA},
                "args": {"type": "object"},
                "command": {"type": "string"},
                "response": {"type": "string"},
            },
            "required": ["tool", "thought", "args", "command", "response"],
        }

    def pick(self, messages):
        try:
            if self.provider == "ollama":
                content = self._pick_ollama(messages)
            else:
                raise LLMError(
                    f"unknown provider: {self.provider!r} "
                    "(core/llm.py serves Ollama only; use ZARS_MODE for Gemini/Groq)"
                )
        except LLMError:
            raise
        except Exception as e:
            raise LLMError(f"model call failed: {e}") from e
        return self._parse_response(content)

    def _pick_ollama(self, messages):
        # Hooked here rather than in the provider layer because agent.py falls
        # back to this object directly whenever no provider is injected
        # (`llm = llm or self.llm`), so this is the one place every Ollama call
        # passes through.
        self._autostart()
        try:
            return self._chat_ollama(messages)
        except Exception as first:
            from .ollama_serve import ensure_serving, server_reachable, RECOVERY_WAIT_S
            if server_reachable(self.config):
                # The server is up, so this is a different failure -- a missing
                # model, a bad tool pick. Retrying would only cost a round trip.
                raise
            # It is gone. Bring it back with a longer budget than start-up:
            # restarting a server that had a model loaded takes measurably
            # longer than a cold one. force=True because the success cache may
            # still vouch for a server that has since died.
            budget = max(float(getattr(self.config, "ollama_autostart_wait_s", 30.0)),
                         RECOVERY_WAIT_S)
            if not ensure_serving(self.config, log=self._log, wait_s=budget,
                                  force=True):
                raise LLMError(
                    f"ollama stopped answering and could not be restarted "
                    f"(original error: {first})"
                ) from first
            return self._chat_ollama(messages)

    def _chat_ollama(self, messages):
        response = self.client.chat(
            model=self.config.model,
            messages=messages,
            format=self.tool_schema(),
            options={
                "temperature": self.config.temperature,
                "num_ctx": self.config.num_ctx,
            },
        )
        return response["message"]["content"]

    def _parse_response(self, content):
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMError(f"model returned invalid JSON: {e}\nraw: {content[:200]}") from e
        tool = data.get("tool")
        if tool not in list(self.registry.entries.keys()) + TOOL_IDS_EXTRA:
            raise LLMError(f"model chose unknown tool: {tool!r}")
        if not isinstance(data.get("args", {}), dict):
            data["args"] = {}
        if data["tool"] == "shell" and not (data.get("command") or "").strip():
            data["command"] = data.get("args", {}).pop("command", "") or data.get("response", "")
        return data
