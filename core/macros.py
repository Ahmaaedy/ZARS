import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


class MacroError(Exception):
    pass


class MacroManager:
    def __init__(self, macros_path=None):
        root = Path(__file__).resolve().parent.parent
        self.path = Path(macros_path) if macros_path else root / "macros.json"
        self.macros = {}
        self.load()

    def load(self):
        if self.path.exists():
            try:
                self.macros = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.macros = {}
        else:
            self.macros = {}

    def save(self):
        self.path.write_text(
            json.dumps(self.macros, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def add_sequence(self, trigger, description, steps):
        trigger = self._normalize_trigger(trigger)
        if trigger in self.macros:
            raise MacroError(f"macro '{trigger}' already exists")
        self.macros[trigger] = {
            "description": description,
            "type": "sequence",
            "trigger": trigger,
            "created": datetime.now(timezone.utc).isoformat(),
            "steps": steps,
        }
        self.save()

    def add_skill(self, trigger, description, skill_path):
        trigger = self._normalize_trigger(trigger)
        if trigger in self.macros:
            raise MacroError(f"macro '{trigger}' already exists")
        self.macros[trigger] = {
            "description": description,
            "type": "skill",
            "trigger": trigger,
            "created": datetime.now(timezone.utc).isoformat(),
            "skill_path": skill_path,
        }
        self.save()

    def delete(self, trigger):
        trigger = self._normalize_trigger(trigger)
        if trigger not in self.macros:
            raise MacroError(f"macro '{trigger}' not found")
        macro = self.macros.pop(trigger)
        if macro.get("type") == "skill" and macro.get("skill_path"):
            skill = Path(macro["skill_path"])
            if skill.exists():
                skill.unlink()
            if skill.parent.exists() and not any(skill.parent.iterdir()):
                skill.parent.rmdir()
        self.save()

    def list(self):
        return dict(self.macros)

    def get(self, trigger):
        trigger = self._normalize_trigger(trigger)
        return self.macros.get(trigger)

    def has(self, trigger):
        trigger = self._normalize_trigger(trigger)
        return trigger in self.macros

    def _normalize_trigger(self, trigger):
        return re.sub(r"[^a-z0-9_]+", "_", trigger.lower().strip()).strip("_")

    def match(self, text):
        t = text.lower().strip()
        for trigger in sorted(self.macros.keys(), key=len, reverse=True):
            if t == trigger or t.startswith(trigger + " "):
                return trigger
        return None


def print_macros(console, macros):
    from rich.table import Table
    from rich import box
    table = Table(title="macros", box=box.SIMPLE)
    table.add_column("trigger", style="cyan")
    table.add_column("type", style="magenta")
    table.add_column("description")
    for name, macro in macros.list().items():
        table.add_row(name, macro["type"], macro["description"])
    console.print(table)


def handle_macro_add(console, macros, registry, config):
    from . import interactive
    trigger = (interactive.ask("What should the trigger word be?") or "").strip()
    if not trigger:
        console.print("[red]cancelled[/red]")
        return
    if macros.has(trigger):
        console.print(f"[red]macro '{trigger}' already exists[/red]")
        return
    description = (interactive.ask(f"What should '{trigger}' do?") or "").strip()
    if not description:
        console.print("[red]cancelled[/red]")
        return
    mode = interactive.ask(
        "Generate a Python skill, or chain existing commands?",
        default="skill", choices=["skill", "sequence"],
    )
    if mode is None:
        console.print("[red]cancelled[/red]")
        return
    if mode == "skill":
        from rich.panel import Panel
        from .skillgen import generate_skill, save_skill
        console.print(Panel("[dim]generating skill...[/dim]", title="skill generation", border_style="blue"))
        skill_json, skill_py = generate_skill(config, description, skill_id=trigger, console=console)
        if skill_json is None or skill_py is None:
            console.print(f"[red]{skill_py or 'generation failed'}[/red]")
            return
        path = save_skill(trigger, skill_json, skill_py)
        macros.add_skill(trigger, description, path)
        console.print(f"[green]macro '{trigger}' saved[/green] (skill: {path})")
    else:
        steps = []
        console.print("[dim]enter steps as: tool_name arg1=value1 arg2=value2[/dim]")
        console.print("[dim]type 'done' when finished[/dim]")
        while True:
            line = interactive.ask("step")
            if line is None:
                console.print("[red]cancelled[/red]")
                return
            line = line.strip()
            if line.lower() == "done":
                break
            parts = line.split()
            if not parts:
                continue
            tool = parts[0]
            args = {}
            for p in parts[1:]:
                if "=" in p:
                    k, v = p.split("=", 1)
                    args[k] = v
            steps.append({"tool": tool, "args": args})
        if not steps:
            console.print("[red]no steps, cancelled[/red]")
            return
        macros.add_sequence(trigger, description, steps)
        console.print(f"[green]macro '{trigger}' saved[/green] ({len(steps)} steps)")


def execute_sequence(config, steps, registry, console, dry_run=False, no_confirm=False):
    results = []
    for i, step in enumerate(steps):
        tool = step.get("tool")
        args = step.get("args", {})
        if tool not in registry.entries:
            results.append({"step": i + 1, "tool": tool, "ok": False, "error": f"unknown tool: {tool}"})
            continue
        entry = registry.entries[tool]
        if dry_run:
            console.print(f"[cyan]step {i + 1}:[/cyan] would run {tool}({args})")
            results.append({"step": i + 1, "tool": tool, "ok": True, "output": "dry-run"})
            continue
        from .agent import Agent
        agent = Agent(config, registry, console, dry_run=False, no_confirm=no_confirm)
        console.print(f"[bold blue]->[/bold blue] [bold]{tool}[/bold]({agent._args_str(args)})")
        result, _ = agent._run_entry(entry, args)
        style = "green" if result.ok else "red"
        mark = "OK" if result.ok else "FAILED"
        console.print(f"  [{mark}] {tool}: {result.display or '(no output)'}")
        results.append({"step": i + 1, "tool": tool, "ok": result.ok, "output": result.display})
        if not result.ok:
            break
    return results


def execute_skill_macro(config, macro, registry, console, dry_run=False, no_confirm=False):
    from .executor import run_skill

    skill_path = macro.get("skill_path")
    if not skill_path or not Path(skill_path).exists():
        return False, f"skill not found: {skill_path}"

    if dry_run:
        console.print(f"[cyan]would run skill:[/cyan] {skill_path}")
        return True, "dry-run"

    handler = {"type": "skill", "run": skill_path, "timeout": 60}
    result = run_skill(config, handler, {})
    return result.ok, result.display
