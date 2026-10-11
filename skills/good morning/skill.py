import sys
from pathlib import Path
import subprocess
from zars_sdk import build_parser, emit, manifest_for

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

def main():
    ns = build_parser(manifest_for(__file__)).parse_args()
    try:
        result = subprocess.run(
            ["powershell", "-Command", "Start-Process 'https://news.google.com'"],
            capture_output=True, text=True, timeout=30
        )
        emit(True, "News page opened")
    except Exception as e:
        emit(False, str(e))

if __name__ == "__main__":
    main()