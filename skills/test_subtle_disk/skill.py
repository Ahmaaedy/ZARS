import sys
import subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zars_sdk import build_parser, emit, manifest_for

def main():
    ns = build_parser(manifest_for(__file__)).parse_args()
    try:
        result = subprocess.run(
            ["powershell", "-Command", f'$path = "{ns.path}"; $info = [IO.DirectoryInfo]::new("{ns.path}"); $bytes = $info.GetAvailableFreeSpace(); Write-Output "FreeSpaceBytes: $bytes"; $gb = [math]::Round($bytes / 1GB, 2); Write-Output "FreeSpaceGB: $gb GB"'],
            capture_output=True, text=True, timeout=30
        )
        emit(True, result.stdout.strip())
    except Exception as e:
        emit(False, str(e))

if __name__ == "__main__":
    main()