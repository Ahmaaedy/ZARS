import json
import string
from dataclasses import dataclass, field
from pathlib import Path

VALID_HANDLER_TYPES = {"shell", "skill", "builtin"}
VALID_RISK = {"safe", "confirm"}
VALID_PARAM_TYPES = {"string", "number", "integer", "boolean"}
RESERVED_VERBS = frozenset({
    "open", "close", "launch", "start", "stop", "set", "run", "toggle",
    "write", "search", "remember", "find",
})


@dataclass
class Entry:
    id: str
    desc: str
    params: list
    keywords: list
    triggers: list
    tier: str
    examples: list
    handler: dict
    risk: str
    source: str

    def required_params(self):
        return [p for p in self.params if p.get("required")]

    def requires_arg(self):
        rp = self.required_params()
        return rp[0]["name"] if rp else None

    def param_line(self):
        if not self.params:
            return "no params"
        parts = []
        for p in self.params:
            req = ", required" if p.get("required") else ""
            parts.append(f"{p['name']} ({p.get('type', 'string')}{req})")
        return "; ".join(parts)


class RegistryError(ValueError):
    pass


class Registry:
    def __init__(self, entries, errors):
        self.entries = entries
        self.errors = errors


def _validate_param(p, where):
    if not isinstance(p, dict) or "name" not in p:
        raise RegistryError(f"{where}: param needs a 'name': {p}")
    if p.get("type", "string") not in VALID_PARAM_TYPES:
        raise RegistryError(f"{where}: bad param type {p.get('type')!r}")


def make_entry(raw, source):
    where = raw.get("id") or source
    if not raw.get("id") or not isinstance(raw["id"], str):
        raise RegistryError(f"{where}: missing 'id'")
    if not raw.get("desc"):
        raise RegistryError(f"{raw['id']}: missing 'desc'")
    if not isinstance(raw.get("params", []), list):
        raise RegistryError(f"{raw['id']}: 'params' must be a list")
    for p in raw.get("params", []):
        _validate_param(p, raw["id"])
    handler = raw.get("handler")
    if not isinstance(handler, dict) or handler.get("type") not in VALID_HANDLER_TYPES:
        raise RegistryError(f"{raw['id']}: handler.type must be one of {sorted(VALID_HANDLER_TYPES)}")
    if handler["type"] == "shell" and not handler.get("command"):
        raise RegistryError(f"{raw['id']}: shell handler needs 'command' template")
    if handler["type"] == "skill" and not handler.get("run"):
        raise RegistryError(f"{raw['id']}: skill handler needs 'run' path")
    if handler["type"] == "builtin" and not handler.get("fn"):
        raise RegistryError(f"{raw['id']}: builtin handler needs 'fn'")
    if handler["type"] == "skill":
        run = Path(handler["run"])
        if not run.is_absolute():
            run = Path(__file__).resolve().parent.parent / handler["run"]
        if not run.exists():
            raise RegistryError(f"{raw['id']}: skill script not found: {handler['run']}")
        handler["run"] = str(run)
    risk = raw.get("risk", "confirm")
    if risk not in VALID_RISK:
        raise RegistryError(f"{raw['id']}: risk must be one of {sorted(VALID_RISK)}")
    tier = raw.get("tier", "skill")
    triggers = raw.get("triggers", raw.get("keywords", []))
    return Entry(
        id=raw["id"],
        desc=raw["desc"],
        params=raw.get("params", []),
        keywords=raw.get("keywords", []),
        triggers=triggers,
        tier=tier,
        examples=raw.get("examples", []),
        handler=handler,
        risk=risk,
        source=source,
    )


def _lint_triggers(entries):
    phrase_owner = {}
    for entry in entries.values():
        for trigger in entry.triggers:
            norm = trigger.strip().lower()
            tokens = norm.split()

            if entry.tier == "skill" and len(tokens) == 1 and tokens[0] in RESERVED_VERBS:
                raise RegistryError(
                    f"skill '{entry.id}' cannot claim reserved core verb "
                    f"'{tokens[0]}' -- use a multi-word phrase instead"
                )

            if norm in phrase_owner:
                prev_id, prev_param = phrase_owner[norm]
                if prev_id != entry.id:
                    my_param = entry.requires_arg()
                    if my_param == prev_param:
                        raise RegistryError(
                            f"trigger '{trigger}' is claimed by both "
                            f"'{prev_id}' and '{entry.id}' with the same param"
                        )
            phrase_owner[norm] = (entry.id, entry.requires_arg())


def load_registry(commands_path=None, skills_dir=None):
    root = Path(__file__).resolve().parent.parent
    commands_path = Path(commands_path) if commands_path else root / "commands.json"
    skills_dir = Path(skills_dir) if skills_dir else root / "skills"

    entries = {}
    errors = []

    if commands_path.exists():
        try:
            raws = json.loads(commands_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            errors.append(f"commands.json unreadable: {e}")
            raws = []
        for raw in raws:
            try:
                entry = make_entry(raw, "commands.json")
                if entry.id in entries:
                    raise RegistryError(f"duplicate id: {entry.id}")
                entries[entry.id] = entry
            except RegistryError as e:
                errors.append(str(e))
    else:
        errors.append(f"commands.json not found at {commands_path}")

    if skills_dir.exists():
        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir() or skill_dir.name.startswith((".", "_")):
                continue
            manifest_path = skill_dir / "skill.json"
            script_path = skill_dir / "skill.py"
            if not manifest_path.exists() or not script_path.exists():
                errors.append(f"{skill_dir.name}: skill folder needs skill.json + skill.py")
                continue
            try:
                raw = json.loads(manifest_path.read_text(encoding="utf-8"))
                raw.setdefault("id", skill_dir.name)
                raw.setdefault("handler", {})
                raw["handler"].setdefault("type", "skill")
                raw["handler"].setdefault("run", str(script_path))
                raw.setdefault("tier", "skill")
                raw.setdefault("triggers", raw.get("keywords", []))
                entry = make_entry(raw, str(skill_dir))
                if entry.id in entries:
                    raise RegistryError(f"duplicate id: {entry.id}")
                entries[entry.id] = entry
            except (json.JSONDecodeError, RegistryError) as e:
                errors.append(f"skill {skill_dir.name}: {e}")

    try:
        _lint_triggers(entries)
    except RegistryError as e:
        errors.append(str(e))

    return Registry(entries, errors)


def render_template(template, args):
    fields = [f for _, f, _, _ in string.Formatter().parse(template) if f]
    missing = [f for f in fields if f not in args]
    if missing:
        raise RegistryError(f"missing params: {', '.join(missing)}")
    safe = {}
    for f in set(fields):
        v = str(args[f])
        if '"' in v:
            raise RegistryError(f'illegal double-quote in param "{f}"')
        safe[f] = v
    return template.format(**safe)
