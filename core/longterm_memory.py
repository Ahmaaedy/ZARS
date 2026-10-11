import re
import time
from pathlib import Path


class LongTermMemory:
    def __init__(self, config):
        self.dir = Path(getattr(config, "memory_dir", "memory"))
        self.conversations_dir = self.dir / "conversations"
        self.facts_dir = self.dir / "facts"

    def _ensure_dirs(self):
        self.conversations_dir.mkdir(parents=True, exist_ok=True)
        self.facts_dir.mkdir(parents=True, exist_ok=True)

    def _slug(self, text, length=40):
        slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
        return slug[:length] or "note"

    def save_conversation_turn(self, user_msg, assistant_response):
        self._ensure_dirs()
        ts = time.strftime("%Y%m%d-%H%M%S")
        slug = self._slug(user_msg)
        name = f"{ts}-{slug}.md"
        path = self.conversations_dir / name

        date_str = time.strftime("%Y-%m-%dT%H:%M:%S")
        content = (
            f"---\n"
            f"type: conversation\n"
            f"date: {date_str}\n"
            f"---\n\n"
            f"## User\n\n{user_msg}\n\n"
            f"## ZARS\n\n{assistant_response}\n"
        )
        path.write_text(content, encoding="utf-8")
        return path

    def save_fact(self, category, content, tags=None):
        self._ensure_dirs()
        ts = time.strftime("%Y%m%d-%H%M%S")
        slug = self._slug(content)
        name = f"{ts}-{slug}.md"
        path = self.facts_dir / name

        date_str = time.strftime("%Y-%m-%dT%H:%M:%S")
        tag_line = ", ".join(tags) if tags else category
        text = (
            f"---\n"
            f"type: fact\n"
            f"category: {category}\n"
            f"date: {date_str}\n"
            f"tags: [{tag_line}]\n"
            f"---\n\n"
            f"{content}\n"
        )
        path.write_text(text, encoding="utf-8")
        return path

    def search(self, query, max_results=10):
        results = []
        q = query.lower()
        for md_file in sorted(self.dir.rglob("*.md"), reverse=True):
            try:
                text = md_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            low = text.lower()
            idx = low.find(q)
            if idx >= 0 or q in md_file.name.lower():
                snippet = text[max(0, idx - 30): idx + 70].replace("\n", " ") if idx >= 0 else text[:80]
                results.append(f"- {md_file.name}: ...{snippet}...")
            if len(results) >= max_results:
                break
        return results

    def get_recent_conversations(self, n=5):
        if not self.conversations_dir.exists():
            return []
        files = sorted(self.conversations_dir.glob("*.md"), reverse=True)[:n]
        out = []
        for f in files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                out.append(f"### {f.stem}\n{text}")
            except OSError:
                continue
        return out

    def count(self):
        total = 0
        for d in [self.conversations_dir, self.facts_dir]:
            if d.exists():
                total += len(list(d.glob("*.md")))
        return total
