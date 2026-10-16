import argparse
import sys

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core import interactive
from core.agent import Agent
from core.config import Config
from core.executor import run_shell
from core.macros import MacroManager, print_macros, handle_macro_add
from core.registry import load_registry
from core.providers.factory import build_llm

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HELP = """[bold]usage[/bold]: type a request, or a slash command
  /help          this help
  /commands      list the loaded command registry
  /macro list    list all macros
  /macro add     start interactive macro definition
  /macro delete <name>  delete a macro
  /memory        show last 5 conversation turns
  /memory clear  clear conversation history
  /memory search <query>  search long-term memory
  /reload        re-scan commands.json and skills/
  /shell <cmd>   run a raw PowerShell command (safety-gated)
  /dry-run       toggle dry-run mode
  /debug         toggle verbose model I/O
  /clear         clear screen
  /exit          quit"""


def print_commands(console, registry):
    table = Table(title="command registry", box=box.SIMPLE)
    table.add_column("id", style="cyan")
    table.add_column("desc")
    table.add_column("params", style="dim")
    table.add_column("risk", style="magenta")
    table.add_column("source", style="dim")
    for entry in registry.entries.values():
        table.add_row(entry.id, entry.desc, entry.param_line(), entry.risk, entry.source)
    console.print(table)


def handle_slash(console, agent, text):
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    if cmd in ("/exit", "/quit"):
        raise SystemExit
    if cmd == "/help":
        console.print(HELP)
    elif cmd == "/commands":
        print_commands(console, agent.registry)
    elif cmd == "/macro":
        sub = arg.split(maxsplit=1)
        subcmd = sub[0].lower() if sub else "list"
        subarg = sub[1].strip() if len(sub) > 1 else ""
        if subcmd == "list":
            print_macros(console, agent.macros)
        elif subcmd == "add":
            handle_macro_add(console, agent.macros, agent.registry, agent.config)
        elif subcmd == "delete":
            if not subarg:
                console.print("[red]usage: /macro delete <name>[/red]")
                return
            try:
                agent.macros.delete(subarg)
                console.print(f"[green]macro '{subarg}' deleted[/green]")
            except Exception as e:
                console.print(f"[red]{e}[/red]")
        else:
            console.print(f"[red]unknown macro command: {subcmd}[/red]")
    elif cmd == "/memory":
        sub = arg.split(maxsplit=1)
        subcmd = sub[0].lower() if sub else "list"
        subarg = sub[1].strip() if len(sub) > 1 else ""
        if subcmd == "clear":
            agent.memory.clear()
            console.print("[green]conversation history cleared[/green]")
        elif subcmd == "search":
            if not subarg:
                console.print("[red]usage: /memory search <query>[/red]")
                return
            results = agent.longterm.search(subarg)
            if results:
                console.print(f"[bold]{len(results)} result(s):[/bold]")
                for r in results:
                    console.print(r)
            else:
                console.print("[dim]no results found[/dim]")
        elif subcmd == "status":
            short_count = agent.memory.count()
            long_count = agent.longterm.count()
            console.print(f"short-term: [cyan]{short_count}[/cyan] messages (max {agent.config.memory_recent})")
            console.print(f"long-term:  [cyan]{long_count}[/cyan] files in memory/")
        else:
            turns = agent.longterm.get_recent_conversations(5)
            if not turns:
                console.print("[dim]no conversation history yet[/dim]")
            else:
                for t in turns:
                    console.print(t)
                    console.print()
    elif cmd == "/reload":
        registry = load_registry()
        for e in registry.errors:
            console.print(f"[red]registry:[/red] {e}")
        agent.reload(registry)
        console.print(f"[green]reloaded[/green] {len(registry.entries)} commands, {len(registry.errors)} error(s)")
    elif cmd == "/shell":
        if not arg:
            console.print("[red]usage: /shell <command>[/red]")
            return
        result, plan = agent._run_shell_freeform(arg)
        agent._show(result, "/shell")
    elif cmd == "/dry-run":
        agent.dry_run = not agent.dry_run
        console.print(f"dry-run: {'[green]ON[/green]' if agent.dry_run else '[red]OFF[/red]'}")
    elif cmd == "/debug":
        agent.debug = not agent.debug
        console.print(f"debug: {'[green]ON[/green]' if agent.debug else '[red]OFF[/red]'}")
    elif cmd == "/clear":
        console.clear()
    else:
        console.print(f"[red]unknown command[/red] {cmd} -- try /help")


def _is_online(mode):
    """True for any cloud mode, including the deprecated 'online' alias."""
    return str(mode or "offline").strip().lower() != "offline"


def run_voice(config, registry, agent, console):
    """Start the offline voice pipeline. Any failure to bring it up (missing
    model, no mic, missing dependency) is reported and returns instead of
    crashing the app."""
    from core.voice.orchestrator import VoiceLoop
    from core.voice.orb_client import OrbClient

    # The orb overlay is a separate process: Qt needs the main thread for its
    # event loop and the voice loop blocks that thread on the microphone. It is
    # decoration, so every failure path here is non-fatal.
    orb = OrbClient(
        enabled=bool(getattr(config, "voice_orb_enabled", True)),
        size=int(getattr(config, "voice_orb_size", 120)),
        transition_ms=int(getattr(config, "voice_orb_transition_ms", 400)),
        settle=float(getattr(config, "voice_orb_settle", 0.25)),
        log=console.print,
        caption=bool(getattr(config, "voice_orb_caption", True)),
        caption_mode=str(getattr(config, "voice_orb_caption_mode", "typewriter")),
        caption_width=int(getattr(config, "voice_orb_caption_width", 360)),
        caption_max_lines=int(getattr(config, "voice_orb_caption_max_lines", 4)),
        caption_fade_ms=int(getattr(config, "voice_orb_caption_fade_ms", 400)),
    ).start()

    try:
        loop = VoiceLoop(config, agent, console=console, orb=orb)
    except Exception as e:
        orb.stop()
        console.print(f"[bold red]voice mode unavailable:[/bold red] {e}")
        console.print("[dim]check voice.* settings in config.json, then run "
                      "`python test_voice.py all` to isolate the stage[/dim]")
        return 1
    # One-line notice: which provider handles each stage, and what leaves the machine.
    console.print(
        f"[dim]mode={config.zars_mode} | wake+vad: local | stt: {loop.stt_name} | "
        f"llm: {loop.llm_name} | tts: local (Kokoro)[/dim]"
    )
    if _is_online(config.zars_mode):
        console.print("[yellow]ONLINE: recorded audio and conversation text are sent to your cloud provider.[/yellow]")
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
        orb.stop()
    return 0


def main():
    ap = argparse.ArgumentParser(prog="zars", description="on-device terminal AI assistant")
    ap.add_argument("--dry-run", action="store_true", help="print actions without executing")
    ap.add_argument("--no-confirm", action="store_true", help="skip confirmation prompts")
    ap.add_argument("--debug", action="store_true", help="verbose model I/O")
    ap.add_argument("--model", help="override model from config.json")
    ap.add_argument("--host", help="override ollama host")
    ap.add_argument("--exec", dest="one_shot", metavar="REQUEST", help="run one request and exit")
    ap.add_argument("--voice", action="store_true", help="offline voice mode (wake word -> STT -> LLM -> TTS)")
    args = ap.parse_args()

    config = Config(model=args.model, host=args.host)
    console = Console(legacy_windows=False)
    interactive.init(console)

    registry = load_registry()
    for e in registry.errors:
        console.print(f"[red]registry:[/red] {e}")
    if not registry.entries:
        console.print("[bold red]no commands loaded, exiting[/bold red]")
        raise SystemExit(1)

    macros = MacroManager()
    agent = Agent(config, registry, console,
                  dry_run=args.dry_run, no_confirm=args.no_confirm, debug=args.debug,
                  macro_manager=macros)

    if args.voice:
        return run_voice(config, registry, agent, console)

    # CLI reasoning honours ZARS_MODE too: build the LLM provider once and pass
    # it to handle(). Offline returns the same OllamaLLM wrapper as before.
    llm_provider, llm_note = build_llm(config, agent, log=console.print)
    llm_name = getattr(llm_provider, "name", "llm")

    if args.one_shot:
        request = args.one_shot.strip()
        if request.startswith("/"):
            handle_slash(console, agent, request)
        else:
            agent.handle(request, llm=llm_provider)
        return

    console.print(Panel.fit(
        f"[bold cyan]ZARS[/bold cyan] on-device terminal assistant\n"
        f"[dim]model: {config.model} | {len(registry.entries)} commands | {len(macros.macros)} macros | "
        f"dry-run: {'on' if agent.dry_run else 'off'}[/dim]\n"
        f"[dim]/help for commands[/dim]",
        border_style="cyan",
    ))
    console.print(f"[dim]mode={config.zars_mode} | llm: {llm_name}[/dim]")
    if _is_online(config.zars_mode):
        console.print("[yellow]ONLINE: your requests and any tool output are sent to your cloud provider.[/yellow]")

    interactive.start_reader()

    while True:
        interactive.ensure_prompt("[bold cyan]zars>[/bold cyan] ")
        try:
            kind, payload = interactive.get_event()
        except KeyboardInterrupt:
            console.print("\n[dim]bye[/dim]")
            return
        if kind == "interrupt":
            console.print("\n[dim]bye[/dim]")
            return
        if kind != "line":
            continue
        text = payload.strip()
        if not text:
            continue
        if text.startswith("/"):
            try:
                handle_slash(console, agent, text)
            except SystemExit:
                console.print("\n[dim]bye[/dim]")
                return
            continue
        try:
            agent.handle(text, llm=llm_provider)
        except KeyboardInterrupt:
            console.print("\n[yellow]interrupted[/yellow]")


if __name__ == "__main__":
    main()
