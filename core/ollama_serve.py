"""Start `ollama serve` on demand, and shut down only what we started.

Zars reaches Ollama over plain HTTP and never launched the server itself, so on
a machine where Ollama is not already running every reasoning call dies with a
connection error. This module makes that self-healing: probe, start if needed,
wait until it answers, then carry on.

Three invariants worth stating, because each one is a way this could do damage:

  * Never spawn for a non-loopback host. config.host may point at a LAN server
    someone else runs; starting a local one would be wrong and useless.
  * Never terminate a server we did not start. If Ollama was already listening,
    or the user runs it as a service or tray app, it is left strictly alone.
  * Never raise. A failed autostart degrades to the same connection error the
    user would have got anyway, rather than a new traceback.

Note that killing `ollama serve` does not guarantee its per-model `runner`
children are reaped on Windows; they normally exit with their parent, but a
loaded model can briefly outlive the server.
"""
import atexit
import ctypes
import json
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

DEFAULT_HOST = "http://localhost:11434"
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
# JobObjectExtendedLimitInformation, the enum SetInformationJobObject expects.
JOBOBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
# A closed loopback port on this machine is filtered rather than refused, so a
# probe that skips the TCP step burns its whole timeout -- twice over, since
# "localhost" is tried on both stacks. Gating on a plain TCP connect first makes
# "server is down" cost ~0.3s instead of ~3s.
TCP_TIMEOUT_S = 0.3
HTTP_TIMEOUT_S = 1.0
POLL_INTERVAL_S = 0.35
# How long a successful check is trusted without re-probing. Measuring on this
# machine: the TCP connect alone costs ~0.31s and the /api/tags GET another
# ~1.0s, so re-checking before every reasoning call added ~1.3s of pure
# overhead to every turn. The chat request is the real liveness test, and a
# failure there re-runs the check, so a short cache costs nothing in safety.
VERIFIED_FOR_S = 120.0
# Restarting a server that had a model loaded is slower than a cold start:
# measured ~4s cold and ~10s after a forced kill of a loaded server, on a slow
# loopback. Recovery is mid-request, so it gets a longer budget than the
# start-up check -- the alternative is failing a request outright.
RECOVERY_WAIT_S = 45.0

# Hosts we are allowed to start a server on. 0.0.0.0 is included because that is
# what a "listen on all interfaces" bind looks like from the client's side.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})

# Where the Windows installer puts the binary. PATH is tried first; these are
# the fallbacks for a GUI install that never added itself to the environment.
_WINDOWS_FALLBACKS = (
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"),
    r"C:\Program Files\Ollama\ollama.exe",
)

_lock = threading.Lock()
_proc = None            # the Popen we started, and the only thing we may stop
_job = None             # (kernel32, handle) for the kill-on-close job, if any
_started_by_us = False
_warned_models = set()  # so a missing model is reported once, not every turn
_verified_until = 0.0   # a successful check is trusted until this time


def _log_default(message):
    print(f"[zars] {message}")


def base_url(host):
    """Normalise a configured host into something urlopen accepts."""
    host = (host or "").strip() or DEFAULT_HOST
    if "://" not in host:
        host = "http://" + host
    return host.rstrip("/")


def is_loopback(url):
    """True when the URL points at this machine."""
    try:
        name = urlsplit(url).hostname or ""
    except ValueError:
        return False
    return name.lower() in LOOPBACK_HOSTS or name.lower().startswith("127.")


def _tcp_open(url, timeout_s=TCP_TIMEOUT_S):
    """True when something accepts a TCP connection on the Ollama port.

    Cheap enough to call on every readiness poll, and it is the only way to
    distinguish "nothing is listening" quickly on a host that drops rather than
    refuses the connection.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname or "localhost"
        port = parts.port or 11434
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except Exception:
        return False


def _fetch_models(url, timeout_s=HTTP_TIMEOUT_S):
    """(is_up, model_names) from a single /api/tags request."""
    if not _tcp_open(url):
        return False, []
    try:
        with urllib.request.urlopen(url + "/api/tags", timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError:
        return True, []               # answered, just not with /api/tags
    except Exception:
        return False, []              # connected but not serving HTTP yet
    names = []
    for entry in payload.get("models") or []:
        name = entry.get("name") or entry.get("model") or ""
        if name:
            names.append(name)
    return True, names


def probe(url, timeout_s=HTTP_TIMEOUT_S):
    """True when something is answering on the Ollama HTTP port."""
    return _fetch_models(url, timeout_s)[0]


def server_reachable(config):
    """Live check, never cached. Used to tell a dead server from another fault."""
    return probe(base_url(getattr(config, "host", DEFAULT_HOST)))


def invalidate():
    """Forget a previous successful check, so the next call re-probes."""
    global _verified_until
    _verified_until = 0.0


def _matches(want, installed):
    """Ollama reports 'zars:latest'; config may say either form."""
    want = want.strip().lower()
    if not want:
        return False
    for name in installed:
        n = name.lower()
        if n == want or n.split(":")[0] == want.split(":")[0]:
            return True
    return False


def has_model(config, model):
    """True when `model` is present on a reachable server."""
    _, installed = _fetch_models(base_url(getattr(config, "host", DEFAULT_HOST)))
    return _matches(model, installed)


def _warn_missing_model(config, installed, log):
    """Say so, once per process, when the configured model is not pulled.

    Starting the server does not help if the model was never pulled, and the raw
    error for that otherwise arrives mid-conversation as an opaque 404.
    """
    model = getattr(config, "model", "")
    if not model or model in _warned_models or _matches(model, installed):
        return
    _warned_models.add(model)
    log(f"ollama is up but the model {model!r} is not pulled yet -- run: "
        f"ollama pull {model}")


def find_ollama():
    """Absolute path to the ollama binary, or None."""
    found = shutil.which("ollama")
    if found:
        return found
    if os.name == "nt":
        for candidate in _WINDOWS_FALLBACKS:
            if candidate and os.path.isfile(candidate):
                return candidate
    return None


def _kill_on_close_job():
    """A Windows Job Object whose processes die when its handle is closed.

    Ollama spawns a per-model `llama-server` runner that holds several hundred MB
    to several GB. Terminating `ollama serve` leaves that child alive holding its
    memory -- measured here: one stranded runner per run, which on a 16 GB
    machine is not survivable. Putting the server in a kill-on-close job makes
    the OS reap the whole tree, and it also covers Zars being killed hard enough
    that atexit never runs.

    Returns (kernel32, job_handle), or (None, None) if unavailable.
    """
    if os.name != "nt":
        return None, None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # Without explicit restypes, ctypes defaults every HANDLE to c_long and
        # truncates it on 64-bit Windows: CreateJobObjectW returned a real handle
        # that came back as 448. Every signature below is declared for that.
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE,
                                                      wintypes.HANDLE]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount",
                "OtherOperationCount", "ReadTransferCount",
                "WriteTransferCount", "OtherTransferCount")]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None, None
        if not kernel32.SetInformationJobObject(
                job, JOBOBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            return None, None
        return kernel32, job
    except Exception:
        return None, None


def _assign_to_job(kernel32, job, proc):
    """Put a just-spawned process into the job. Best effort.

    The window between CreateProcess and this call is small and Ollama does not
    spawn its runner until a model is requested, which is much later, so nothing
    realistically escapes the job.
    """
    if not kernel32 or not job or proc is None:
        return False
    try:
        return bool(kernel32.AssignProcessToJobObject(job,
                                                      int(proc._handle)))
    except Exception:
        return False


def _tree_kill(pid):
    """Fallback when no job object: kill the process tree by PID."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=30)
        else:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception:
        pass


def _spawn(exe):
    """Launch `ollama serve` with no console window, inside a kill-on-close job."""
    kwargs = dict(stdin=subprocess.DEVNULL,
                  stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL,
                  close_fds=True)
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        # Own session so the whole group can be signalled at once.
        kwargs["start_new_session"] = True
    proc = subprocess.Popen([exe, "serve"], **kwargs)
    kernel32, job = _kill_on_close_job()
    assigned = _assign_to_job(kernel32, job, proc)
    return proc, (kernel32, job if assigned else None)


def _stop_started():
    """Terminate only the server we launched. Never raises.

    This is registered with atexit, so an exception escaping here would print a
    traceback during interpreter shutdown. Every step is guarded.

    Order matters: closing the job handle is what reaps the runner children, so
    it happens first. terminate() stays as a belt-and-braces for the case where
    no job object was available.
    """
    global _proc, _started_by_us, _verified_until, _job
    _verified_until = 0.0             # never trust the cache after we stop one
    with _lock:
        proc, _proc, _started_by_us = _proc, None, False
        job, _job = _job, None
    if proc is None:
        if job:
            _close_job(job)
        return
    try:
        if proc.poll() is not None:
            # Already exited; nothing to kill. Closing the job is still safe and
            # would reap anything it left behind.
            if job:
                _close_job(job)
            return
    except Exception:
        return
    if job:
        _close_job(job)
    else:
        # No job object (POSIX, or Windows refused one): kill the tree directly,
        # otherwise the runner child survives holding the model's memory.
        try:
            _tree_kill(proc.pid)
        except Exception:
            pass
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass
    except Exception:
        pass


def _close_job(job):
    kernel32, handle = job
    if not kernel32 or not handle:
        return
    try:
        # KILL_ON_JOB_CLOSE terminates every process still in the job.
        kernel32.CloseHandle(handle)
    except Exception:
        pass


atexit.register(_stop_started)


def _wait_ready(url, wait_s, proc=None):
    """Poll until Ollama answers HTTP, or the budget runs out.

    Returns (ready, reason). The reason is user-facing: a child that dies on
    the spot (port still held by the instance we just stopped) reads very
    differently from a timeout, and guessing wrong costs the user the only
    useful clue.

    Ollama opens its port almost immediately but needs a few seconds before it
    will serve, so the port check and the HTTP check are separate steps rather
    than one probe.
    """
    deadline = time.monotonic() + max(0.0, wait_s)
    while True:
        if probe(url):
            return True, None
        if proc is not None and proc.poll() is not None:
            return False, (f"`ollama serve` exited immediately (code "
                           f"{proc.returncode}) -- another Ollama may still be "
                           f"shutting down and holding the port")
        if time.monotonic() >= deadline:
            return False, (f"ollama did not answer within {wait_s:g}s; if it is "
                           f"loading a large model this can take a while")
        time.sleep(POLL_INTERVAL_S)


def ensure_serving(config, log=None, wait_s=None, force=False):
    """Make sure Ollama is answering. Returns True if it is.

    Idempotent, and cheap when the server is already up: one TCP connect plus
    one GET, and only until VERIFIED_FOR_S elapses. Spawning happens at most
    once per process.

    `force=True` skips the cached verdict. The retry path in core/llm.py needs
    that: if the server died inside the cache window, trusting the cache would
    return True without restarting anything.
    """
    global _proc, _started_by_us, _verified_until
    log = log or _log_default
    url = base_url(getattr(config, "host", DEFAULT_HOST))

    if not force and time.monotonic() < _verified_until:
        return True

    up, installed = _fetch_models(url)
    if up:
        # Already running -- including one we did not start. Report a missing
        # model here too: that is the common case, and it is not fixed by
        # starting anything.
        _warn_missing_model(config, installed, log)
        _verified_until = time.monotonic() + VERIFIED_FOR_S
        return True
    if not getattr(config, "ollama_autostart", True):
        return False
    if not is_loopback(url):
        # Someone else's server. Say so instead of starting a rival locally.
        log(f"ollama not reachable at {url} and that is not this machine; "
            f"not starting one")
        return False

    exe = find_ollama()
    if not exe:
        log(f"ollama is not running and the binary was not found on PATH; "
            f"install it from ollama.com or put it on PATH")
        return False

    if wait_s is None:
        wait_s = float(getattr(config, "ollama_autostart_wait_s", 30.0) or 30.0)

    global _proc, _job, _started_by_us
    with _lock:
        if _proc is not None and _proc.poll() is None:
            proc = _proc             # one we already started, still coming up
        else:
            try:
                proc, job = _spawn(exe)
            except OSError as e:
                log(f"could not start ollama: {e}")
                _proc = None
                _job = None
                _started_by_us = False
                return False
            _proc = proc
            _job = job
            _started_by_us = True
            log("ollama is not running -- starting `ollama serve`")

    ready, reason = _wait_ready(url, wait_s, proc)
    if not ready:
        log(reason)
        return False

    _, installed = _fetch_models(url)
    _warn_missing_model(config, installed, log)
    _verified_until = time.monotonic() + VERIFIED_FOR_S
    return True


def shutdown():
    """Stop the server we started, if any. Public for tests and clean exits."""
    _stop_started()


def started_by_us():
    """True while we own a running server process."""
    with _lock:
        return _started_by_us and _proc is not None and _proc.poll() is None