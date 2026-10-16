import sys
import subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zars_sdk import build_parser, emit, manifest_for

def main():
    ns = build_parser(manifest_for(__file__)).parse_args()
    try:
        location = ns.location
        units = ns.units if ns.units else ""
        location_enc = location.replace(" ", "%20")
        url = f"https://weather.com/weather/today/l/{location_enc}"
        if units:
            url += f"?unit={units}"
        ps_cmd = f'Start-Process "{url}"; Write-Output "Opened weather for {location}"'
        result = subprocess.run(
            ["powershell", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=30
        )
        emit(True, result.stdout.strip())
    except Exception as e:
        emit(False, str(e))

if __name__ == "__main__":
    main()