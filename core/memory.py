import json
import time
from pathlib import Path


class ConversationMemory:
    def __init__(self, config):
        self.dir = Path(getattr(config, "memory_dir", "memory"))
        self.max_messages = getattr(config, "memory_recent", 20)
        self.file = self.dir / "recent.json"
        self._messages = []
        self._load()

    def _load(self):
        if self.file.exists():
            try:
                self._messages = json.loads(self.file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._messages = []

    def _save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._messages, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.file)

    def add(self, role, content):
        self._messages.append({
            "role": role,
            "content": content,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        if len(self._messages) > self.max_messages:
            self._messages = self._messages[-self.max_messages:]
        self._save()

    def get_recent(self, n=None):
        n = n or self.max_messages
        return self._messages[-n:]

    def get_all(self):
        return list(self._messages)

    def clear(self):
        self._messages = []
        self._save()

    def count(self):
        return len(self._messages)

    def format_for_prompt(self, n=None):
        msgs = self.get_recent(n)
        if not msgs:
            return ""
        lines = ["Previous conversation context:"]
        for m in msgs:
            tag = "User" if m["role"] == "user" else "ZARS"
            lines.append(f"{tag}: {m['content'][:500]}")
        return "\n".join(lines)
