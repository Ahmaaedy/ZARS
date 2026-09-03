import json
from pathlib import Path

DEFAULTS = {
    "model": "qwen2.5:3b-instruct",
    "host": "http://localhost:11434",
    "temperature": 0.1,
    "step_budget": 4,
    "output_head": 2000,
    "output_tail": 1200,
    "shell_timeout": 30,
    "skill_timeout": 60,
    "notes_dir": "notes",
    "num_ctx": 8192,
}


class Config:
    def __init__(self, path="config.json", **overrides):
        data = dict(DEFAULTS)
        p = Path(path)
        if p.exists():
            data.update(json.loads(p.read_text(encoding="utf-8")))
        data.update({k: v for k, v in overrides.items() if v is not None})
        for k, v in data.items():
            setattr(self, k, v)

    def truncate_output(self, text):
        h, t = self.output_head, self.output_tail
        if len(text) <= h + t:
            return text
        return text[:h] + f"\n...[{len(text) - h - t} chars truncated]...\n" + text[-t:]
