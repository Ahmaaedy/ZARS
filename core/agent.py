import json

from rich.panel import Panel

from . import interactive
from .executor import ExecError, ExecResult, execute, run_shell
from .llm import LLM, LLMError
from .longterm_memory import LongTermMemory
from .memory import ConversationMemory
from .registry import RegistryError, render_template
from .router import Router
from .safety import check_blocked
from .session import Session


class Agent:
    def __init__(self, config, registry, console, dry_run=False, no_confirm=False, debug=False, macro_manager=None):
        self.config = config
        self.registry = registry
        self.console = console
        self.dry_run = dry_run
        self.no_confirm = no_confirm
        self.debug = debug
        self.macros = macro_manager
        self.router = Router(registry, macro_manager)
        self.llm = LLM(config, registry)
        self.memory = ConversationMemory(config)
        self.longterm = LongTermMemory(config)
        self._confirm_rejections = 0

    def reload(self, registry):
        self.registry = registry
        self.router = Router(registry, self.macros)
        self.llm = LLM(self.config, registry)

    def handle(self, text):
        entry, args = self.router.match(text)
        if entry:
            self.console.print(f"[bold blue]->[/bold blue] [bold]{entry.id}[/bold]({self._args_str(args)}) [dim](core)[/dim]")
            result, _ = self._run_entry(entry, args)
            self._show(result, entry.id)
            return
        if self.macros:
            macro_trigger = self.macros.match(text)
            if macro_trigger:
                self._run_macro(macro_trigger)
                return
        if self._handle_macro_intent(text):
            return
        self._llm_loop(text)

    def handle_voice(self, text, llm=None):
        """Same routing as handle(), but returns the final answer as a string
        so the voice loop can speak it. handle() ignores the return value, so
        this is additive.

        `llm` lets the voice pipeline inject a provider (local or Groq) without
        mutating self.llm, so CLI requests always use the configured backend.
        """
        entry, args = self.router.match(text)
        if entry:
            self.console.print(f"[bold blue]->[/bold blue] [bold]{entry.id}[/bold]({self._args_str(args)}) [dim](core)[/dim]")
            result, _ = self._run_entry(entry, args)
            self._show(result, entry.id)
            return result.display or ""
        if self.macros:
            macro_trigger = self.macros.match(text)
            if macro_trigger:
                self._run_macro(macro_trigger)
                return f"{macro_trigger} done"
        if self._handle_macro_intent(text):
            return ""
        return self._llm_loop(text, llm=llm) or ""

    def _handle_macro_intent(self, text):
        t = text.lower().strip()
        create_patterns = ["set a macro", "create a macro", "new macro", "add a macro", "make a macro", "i want to set a macro", "i want to create a macro"]
        list_patterns = ["list macros", "show macros", "what macros do i have", "show my macros"]
        for p in create_patterns:
            if p in t:
                from .macros import handle_macro_add
                handle_macro_add(self.console, self.macros, self.registry, self.config)
                return True
        for p in list_patterns:
            if p in t:
                from .macros import print_macros
                print_macros(self.console, self.macros)
                return True
        delete_match = None
        for p in ["delete the ", "remove the "]:
            if p in t:
                rest = t.split(p, 1)[1]
                rest = rest.replace("macro", "").strip().strip('"').strip("'")
                delete_match = rest
                break
        if delete_match:
            try:
                self.macros.delete(delete_match)
                self.console.print(f"[green]macro '{delete_match}' deleted[/green]")
            except Exception as e:
                self.console.print(f"[red]{e}[/red]")
            return True
        return False

    def _run_macro(self, trigger):
        macro = self.macros.get(trigger)
        if not macro:
            self.console.print(f"[red]macro '{trigger}' not found[/red]")
            return
        self.console.print(f"[bold magenta]->[/bold magenta] [bold]{trigger}[/bold] [dim](macro: {macro['type']})[/dim]")
        if macro["type"] == "sequence":
            from .macros import execute_sequence
            execute_sequence(self.config, macro["steps"], self.registry, self.console, self.dry_run, self.no_confirm)
        elif macro["type"] == "skill":
            from .macros import execute_skill_macro
            ok, output = execute_skill_macro(self.config, macro, self.registry, self.console, self.dry_run, self.no_confirm)
            style = "green" if ok else "red"
            mark = "OK" if ok else "FAILED"
            self.console.print(Panel(output or "(no output)", title=f"{mark} {trigger}", border_style=style))

    # ---------- safety ----------

    def _confirm(self, plan):
        if self.no_confirm:
            return True
        if self._confirm_rejections >= 2:
            self.console.print("[bold red]auto-denied:[/bold red] too many rejections this session")
            return False
        self.console.print(Panel(plan, title="confirm", border_style="yellow"))
        try:
            answer = interactive.ask("run this? [y/N]", default="n")
        except (KeyboardInterrupt, EOFError):
            answer = "n"
        if answer is None:
            answer = "n"
        ok = answer.strip().lower() in ("y", "yes")
        if not ok:
            self._confirm_rejections += 1
        return ok

    def _guard(self, plan, risk):
        blocked, reason = check_blocked(plan) if plan.startswith("powershell:") else (False, None)
        if blocked:
            self.console.print(f"[bold red]blocked:[/bold red] {reason}")
            return "blocked", f"blocked by safety policy: {reason}"
        if self.dry_run:
            self.console.print(f"[cyan]would run:[/cyan] {plan}")
            return "dry", None
        if risk != "safe" and not self._confirm(plan):
            self.console.print("[yellow]aborted[/yellow]")
            return "abort", None
        return "run", None

    # ---------- execution ----------

    def _plan_for_entry(self, entry, args):
        handler = entry.handler
        if handler["type"] == "shell":
            try:
                command = render_template(handler["command"], args or {})
            except RegistryError as e:
                return None, f"error: {e}"
            return f"powershell: {command}", None
        if handler["type"] == "skill":
            arg_str = " ".join(f"--{k} {v}" for k, v in (args or {}).items())
            return f"python {handler['run']} {arg_str}".strip(), None
        return f"{handler['fn']}({args or ''})", None

    def _run_entry(self, entry, args):
        plan, err = self._plan_for_entry(entry, args)
        if err:
            return ExecResult(False, -1, "", err), None
        status, reason = self._guard(plan, entry.risk)
        if status != "run":
            return ExecResult(status == "dry", -1, "", f"{status}: {plan}"), plan
        result, _ = execute(self.config, entry, args)
        return result, plan

    def _run_shell_freeform(self, command, failed_commands=None):
        command = (command or "").strip()
        if not command:
            return ExecResult(False, -1, "", "error: empty command"), None
        if failed_commands and command in failed_commands:
            self.console.print(f"[bold red]auto-denied:[/bold red] this command already failed")
            return ExecResult(False, -1, "", f"auto-denied: already failed: {command}"), None
        plan = f"powershell: {command}"
        status, reason = self._guard(plan, "confirm")
        if status != "run":
            return ExecResult(status == "dry", -1, "", f"{status}: {plan}"), plan
        result = run_shell(self.config, command)
        result.model_text = f"command: {command}\n{result.model_text}"
        return result, plan

    def _show(self, result, title):
        style = "green" if result.ok else "red"
        mark = "OK" if result.ok else "FAILED"
        body = result.display or "(no output)"
        self.console.print(Panel(body, title=f"{mark} {title}", border_style=style))

    # ---------- llm loop ----------

    def _validate_args(self, entry, args):
        known = {p["name"] for p in entry.params}
        clean = {}
        for k, v in (args or {}).items():
            if k in known:
                clean[k] = v
        for p in entry.params:
            if p["name"] not in clean:
                continue
            v = clean[p["name"]]
            if p.get("type") in ("integer", "number"):
                try:
                    clean[p["name"]] = int(v) if p.get("type") == "integer" else float(v)
                except (TypeError, ValueError):
                    return None, f"param {p['name']} for {entry.id} must be a number, got {v!r}"
            elif p.get("type") == "boolean":
                clean[p["name"]] = bool(v)
        missing = [p["name"] for p in entry.required_params() if p["name"] not in clean]
        if missing:
            return None, f"missing required params for {entry.id}: {', '.join(missing)}"
        return clean, None

    def _args_str(self, args):
        if not args:
            return ""
        return ", ".join(f"{k}={v!r}" for k, v in args.items())

    def _llm_loop(self, text, llm=None):
        llm = llm or self.llm
        session = Session(llm.system_prompt())
        recent = self.memory.get_recent(10)
        if recent:
            session.seed(recent)
        session.add("user", text)
        last_call = None
        failed_commands = set()
        confirm_count = 0

        for step in range(1, self.config.step_budget + 1):
            try:
                pick = llm.pick(session.messages)
            except LLMError as e:
                self.console.print(f"[bold red]llm error:[/bold red] {e}")
                return
            if self.debug:
                self.console.print(f"[dim]step {step} | {json.dumps(pick, ensure_ascii=False)}[/dim]")

            tool = pick["tool"]

            if tool == "done":
                resp = (pick.get("response") or "").strip()
                if resp:
                    self.console.print(resp)
                    self.memory.add("user", text)
                    self.memory.add("assistant", resp)
                    self.longterm.save_conversation_turn(text, resp)
                    return resp
                session.add("assistant", json.dumps(pick, ensure_ascii=False))
                session.tool_result("your response was empty. Summarize the results for the user now as JSON with tool=done.")
                continue
            if tool == "ask":
                resp = pick.get("response") or "clarification needed"
                self.console.print(f"[bold yellow]{resp}[/bold yellow]")
                self.memory.add("user", text)
                self.memory.add("assistant", resp)
                self.longterm.save_conversation_turn(text, resp)
                return resp

            if tool == "shell":
                result, plan = self._run_shell_freeform(pick.get("command", ""), failed_commands)
                self._show(result, "shell")
                if not result.ok and plan:
                    cmd = pick.get("command", "").strip()
                    if cmd:
                        failed_commands.add(cmd)
            else:
                entry = self.registry.entries[tool]
                args, problem = self._validate_args(entry, pick.get("args"))
                if problem:
                    result = ExecResult(False, -1, "", f"error: {problem}")
                    self.console.print(f"[bold blue]->[/bold blue] [bold]{tool}[/bold] [red]invalid args[/red]")
                else:
                    self.console.print(f"[bold blue]->[/bold blue] [bold]{tool}[/bold]({self._args_str(args)})")
                    result, plan = self._run_entry(entry, args)
                    self._show(result, tool)

            session.add("assistant", json.dumps(pick, ensure_ascii=False))
            session.tool_result(self.config.truncate_output(result.model_text))

            call_sig = (tool, json.dumps(pick.get("args", {}), sort_keys=True), (pick.get("command") or "").strip())
            if call_sig == last_call:
                self.console.print("[yellow]model repeated the same action once; stopping[/yellow]")
                if call_sig == last_call:
                        self.console.print("[yellow]model repeated the same action twice; stopping[/yellow]")
                        return
            last_call = call_sig

            if not result.ok and result.model_text.startswith(("blocked", "abort")):
                return

        self.console.print(f"[yellow]step budget reached ({self.config.step_budget})[/yellow]")
        return ""