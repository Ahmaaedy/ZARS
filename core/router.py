import re

DEFER_MARKERS = (" and ", " then ", " also ", " but ", " or ")
_URL_RE = re.compile(r"^https?://|^\w+\.\w+")

def _tokenize(text):
    tokens = []
    i = 0
    for raw in text.split(" "):
        start = text.index(raw, i)
        end = start + len(raw)
        if raw:
            tokens.append((raw.lower(), start, end))
        i = end + 1
    return tokens


class Router:
    def __init__(self, registry, macro_manager=None):
        self.registry = registry
        self.macros = macro_manager
        self.core_entries = [
            e for e in registry.entries.values() if e.tier == "core"
        ]

    def match(self, text):
        t = text.strip()
        if not t:
            return None, None

        if any(marker in t.lower() for marker in DEFER_MARKERS) or "?" in t:
            return None, None

        tokens = _tokenize(t)
        if not tokens:
            return None, None

        candidates = []
        for entry in self.core_entries:
            for trigger in entry.triggers:
                trig_tokens = trigger.lower().split()
                n = len(trig_tokens)
                if n > len(tokens):
                    continue
                match = True
                for i, tt in enumerate(trig_tokens):
                    tok = tokens[i][0]
                    if i == n - 1:
                        if not tok.startswith(tt):
                            match = False
                            break
                    elif tok != tt:
                        match = False
                        break
                if not match:
                    continue
                arg = t[tokens[n - 1][2]:].strip(" ?.!") if n <= len(tokens) else ""
                param_name = entry.requires_arg()
                if param_name and not arg:
                    continue
                candidates.append((n, entry, param_name, arg))

        if not candidates:
            return None, None

        max_n = max(c[0] for c in candidates)
        top = [c for c in candidates if c[0] == max_n]

        if len(top) == 1:
            _, entry, param_name, arg = top[0]
            return entry, {param_name: arg} if param_name else {}

        arg = top[0][3]
        if _URL_RE.match(arg):
            for _, entry, param_name, _ in top:
                if param_name and ("url" in param_name.lower()):
                    return entry, {param_name: arg}

        _, entry, param_name, arg = top[0]
        return entry, {param_name: arg} if param_name else {}
