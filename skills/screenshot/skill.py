import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zars_sdk import build_parser, emit, manifest_for


def main():
    ns = build_parser(manifest_for(__file__)).parse_args()
    if ns.out:
        out = Path(ns.out)
    else:
        out = Path.home() / "Pictures" / f"zars_shot_{time.strftime('%Y%m%d-%H%M%S')}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out_str = str(out).replace("'", "''")

    ps = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
        "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
        "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
        "$g=[System.Drawing.Graphics]::FromImage($bmp);"
        "$g.CopyFromScreen($b.X,$b.Y,0,0,$bmp.Size);"
        f"$bmp.Save('{out_str}');"
        "$g.Dispose();$bmp.Dispose()"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        emit(False, error="screenshot timed out")
    if proc.returncode != 0 or not out.exists():
        err = (proc.stderr or "").strip()[:300]
        emit(False, error=f"screenshot failed: {err or 'file not created'}")
    emit(True, {"path": str(out)})


if __name__ == "__main__":
    main()