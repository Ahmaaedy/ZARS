import sys
import subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zars_sdk import build_parser, emit, manifest_for

EXCLUDE = {
    'python', 'python3', 'pwsh', 'powershell', 'code',
    'explorer', 'svchost', 'csrss', 'wininit', 'winlogon',
    'smss', 'lsass', 'services', 'dwm', 'fontdrvhost',
    'zars', 'python', 'terminal'
}

def main():
    ns = build_parser(manifest_for(__file__)).parse_args()
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "Get-Process | Where-Object {$_.MainWindowTitle -ne ''} | Select-Object -ExpandProperty Name"],
            capture_output=True, text=True, timeout=30
        )
        processes = [line.strip().lower() for line in result.stdout.strip().split('\n') if line.strip()]
        closed = []
        failed = []
        skipped = []
        for proc in processes:
            if proc in EXCLUDE:
                skipped.append(proc)
                continue
            try:
                subprocess.run(["taskkill", "/F", "/IM", f"{proc}.exe"],
                             capture_output=True, timeout=10)
                closed.append(proc)
            except Exception:
                failed.append(proc)
        emit(True, {"closed": closed, "failed": failed, "skipped": skipped, "total_closed": len(closed)})
    except Exception as e:
        emit(False, {"error": str(e)})

if __name__ == "__main__":
    main()
