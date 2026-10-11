import sys
import subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zars_sdk import build_parser, emit, manifest_for

def main():
    ns = build_parser(manifest_for(__file__)).parse_args()
    try:
        ps = r"""
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32 {
    [DllImport("user32.dll")]
    public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);
    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
}
"@

Add-Type -AssemblyName System.Windows.Forms
$screens = [System.Windows.Forms.Screen]::AllScreens
$secondary = $screens | Where-Object { -not $_.Primary } | Select-Object -First 1
$main = $screens | Where-Object { $_.Primary } | Select-Object -First 1

function Wait-Window {
    param([System.Diagnostics.Process]$Proc, [int]$MaxWait = 10)
    for ($i = 0; $i -lt $MaxWait * 10; $i++) {
        if ($Proc.MainWindowHandle -ne [IntPtr]::Zero) { return $true }
        Start-Sleep -Milliseconds 100
        $Proc.Refresh()
    }
    return $false
}

function Move-App {
    param([string]$Exe, [int]$X, [int]$Y, [int]$W, [int]$H)
    $proc = Start-Process -FilePath $Exe -PassThru
    if (Wait-Window -Proc $proc) {
        Start-Sleep -Milliseconds 300
        [Win32]::ShowWindow($proc.MainWindowHandle, 9) | Out-Null
        [Win32]::SetWindowPos($proc.MainWindowHandle, [IntPtr]::new(-1), $X, $Y, $W, $H, 0x0040) | Out-Null
    }
    return $proc
}

if ($secondary) {
    $secW = [math]::Floor($secondary.Bounds.Width / 2)
    $secH = $secondary.Bounds.Height
    $secX = $secondary.Bounds.Left
    $secY = $secondary.Bounds.Top

    Move-App -Exe "brave" -X ($secX + $secW) -Y $secY -W $secW -H $secH
    Move-App -Exe "notepad" -X $secX -Y $secY -W $secW -H $secH
}

if ($main) {
    $mainW = $main.Bounds.Width
    $mainH = $main.Bounds.Height
    $mainX = $main.Bounds.Left
    $mainY = $main.Bounds.Top

    Move-App -Exe "code" -X $mainX -Y $mainY -W $mainW -H $mainH
} else {
    Start-Process "code"
}

Write-Output "done"
"""
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            emit(True, "Opened brave, notes, and vscode")
        else:
            err = result.stderr.strip() or "unknown error"
            emit(False, f"partial failure: {err}")
    except Exception as e:
        emit(False, str(e))

if __name__ == "__main__":
    main()
