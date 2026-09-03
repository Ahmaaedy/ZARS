import json

from ollama import Client

TOOL_IDS_EXTRA = ["shell", "done", "ask"]


class LLMError(Exception):
    pass


class LLM:
    def __init__(self, config, registry):
        self.config = config
        self.registry = registry
        self.client = Client(host=config.host)

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
            response = self.client.chat(
                model=self.config.model,
                messages=messages,
                format=self.tool_schema(),
                options={
                    "temperature": self.config.temperature,
                    "num_ctx": self.config.num_ctx,
                },
            )
        except Exception as e:
            raise LLMError(f"model call failed: {e}") from e
        content = response["message"]["content"]
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