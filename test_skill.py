
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from zars_sdk import build_parser, emit, manifest_for
import subprocess
from pathlib import Path

def main():
    ns = build_parser(manifest_for(__file__)).parse_args()
    try:
        result = subprocess.run(
            ["powershell", "-Command", f"""
$displays = [System.Windows.Forms.Screen]::AllScreens
$secondary = $displays[1].Bounds
$halfWidth = [math]::Floor($secondary.Width / 2)
$halfHeight = [math]::Floor($secondary.Height / 2)

Start-Sleep -Seconds 1

Start-Process -FilePath \"brave\" -WindowStyle Hidden | ForEach-Object {{ $_.MainWindowHandle = [System.IntPtr]::new($secondary.Left + $halfWidth, $secondary.Top + $halfHeight) }}

Start-Process -FilePath \"notepad\" -WindowStyle Hidden | ForEach-Object {{ $_.MainWindowHandle = [System.IntPtr]::new($secondary.Left, $secondary.Top, $halfWidth, $halfHeight) }}
