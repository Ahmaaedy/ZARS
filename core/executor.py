import json
import subprocess
import sys
from dataclasses import dataclass

from .builtins import BUILTINS
from .registry import RegistryError, render_template


class ExecError(Exception):
    pass


@dataclass
class ExecResult:
    ok: bool
    exit_code: int
    display: str
    model_text: str


def run_shell(config, command, timeout=None):
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout or config.shell_timeout,
        )
    except subprocess.TimeoutExpired:
        return ExecResult(False, -1, "", f"timed out after {timeout or config.shell_timeout}s")
    except FileNotFoundError:
        return ExecResult(False, -1, "", "powershell not found")
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    ok = proc.returncode == 0
    if out and err:
        display = f"{out}\n[stderr] {err}"
    else:
        display = out or err or "(no output)"
    model_text = f"exit_code: {proc.returncode}"
    if out:
        model_text += f"\nstdout:\n{out}"
    if err:
        model_text += f"\nstderr:\n{err}"
    return ExecResult(ok, proc.returncode, display, model_text)


def run_skill(config, handler, args):
    cmd = [sys.executable, handler["run"]]
    for k, v in (args or {}).items():
        cmd += [f"--{k}", str(v)]
    timeout = handler.get("timeout") or config.skill_timeout
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return ExecResult(False, -1, "", f"skill timed out after {timeout}s")
    except OSError as e:
        return ExecResult(False, -1, "", f"failed to launch skill: {e}")
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    try:
        payload = json.loads(out)
        ok = bool(payload.get("ok"))
        if payload.get("error"):
            display = f"error: {payload['error']}"
            model_text = f"exit_code: {proc.returncode}\nerror: {payload['error']}"
        else:
            data = json.dumps(payload.get("data"), indent=2, ensure_ascii=False) if isinstance(payload.get("data"), (dict, list)) else str(payload.get("data"))
            display = data or "(no output)"
            model_text = f"exit_code: {proc.returncode}\nresult: {data}"
    except json.JSONDecodeError:
        ok = proc.returncode == 0
        display = out or err or "(no output)"
        model_text = f"exit_code: {proc.returncode}\nstdout:\n{out}\nstderr:\n{err}"
    return ExecResult(ok, proc.returncode, display, model_text)


def run_builtin(config, handler, args):
    fn = BUILTINS.get(handler["fn"])
    if fn is None:
        return ExecResult(False, -1, "", f"unknown builtin: {handler['fn']}")
    try:
        ok, output = fn(config, **(args or {}))
    except TypeError as e:
        return ExecResult(False, -1, "", f"bad params: {e}")
    except Exception as e:
        return ExecResult(False, -1, "", f"builtin failed: {e}")
    return ExecResult(ok, 0 if ok else 1, output, f"exit_code: {0 if ok else 1}\nresult: {output}")


def execute(config, entry, args):
    handler = entry.handler
    try:
        if handler["type"] == "shell":
            command = render_template(handler["command"], args or {})
            result = run_shell(config, command, handler.get("timeout"))
            result.model_text = f"command: {command}\n{result.model_text}"
            return result, command
        if handler["type"] == "skill":
            return run_skill(config, handler, args), None
        if handler["type"] == "builtin":
            return run_builtin(config, handler, args), f"{handler['fn']}({args or ''})"
    except RegistryError as e:
        return ExecResult(False, -1, "", f"error: {e}"), None
    return ExecResult(False, -1, "", "unknown handler type"), None