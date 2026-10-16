import sys
import subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zars_sdk import build_parser, emit, manifest_for

def main():
    ns = build_parser(manifest_for(__file__)).parse_args()
    try:
        # Build PowerShell script lines
        lines = []
        lines.append(f"$threshold_gb = {ns.threshold_gb}")
        if ns.temp_dir:
            lines.append(f'$temp_dir = "{ns.temp_dir}"')
        else:
            lines.append("$temp_dir = $env:TEMP")
        dry_run_ps = '$true' if ns.dry_run else '$false'
        lines.append(f"$dry_run = {dry_run_ps}")
        lines.append("$driveLetter = $temp_dir.Substring(0,1)")
        lines.append("$freeBytes = (Get-Volume -DriveLetter $driveLetter).SizeRemaining")
        lines.append("$freeGB = [math]::Round($freeBytes / 1GB, 2)")
        lines.append("if ($freeGB -lt $threshold_gb) {")
        lines.append('    Write-Host "Free space: $freeGB GB"')
        lines.append("    if ($dry_run) {")
        lines.append('        Write-Host "Dry run: would clean temp dir $temp_dir"')
        lines.append("    } else {")
        lines.append('        Write-Host "Cleaning temp dir $temp_dir"')
        lines.append('        Remove-Item -Path "$temp_dir\\*" -Recurse -Force -ErrorAction SilentlyContinue')
        lines.append("    }")
        lines.append("} else {")
        lines.append('    Write-Host "Free space $freeGB GB above threshold $threshold_gb GB, no cleanup needed."')
        lines.append("}")
        ps_script = "\n".join(lines)
        result = subprocess.run(
            ["powershell", "-Command", ps_script],
            capture_output=True, text=True, timeout=30
        )
        emit(True, result.stdout.strip())
    except Exception as e:
        emit(False, str(e))

if __name__ == "__main__":
    main()