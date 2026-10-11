import sys
import subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zars_sdk import build_parser, emit, manifest_for

def main():
    ns = build_parser(manifest_for(__file__)).parse_args()
    try:
        inc_gpu_ps = '$true' if ns.IncludeGPU else '$false'
        output_fmt = (ns.OutputFormat or 'text').lower()
        filter_val = ns.FilterProcess if ns.FilterProcess is not None else ''

        ps_script = (
            f"$IncludeGPU = {inc_gpu_ps}\n"
            f"$OutputFormat = '{output_fmt}'\n"
            f"$FilterProcess = '{filter_val}'\n"
            "\n"
            "# CPU temperature via WMI thermal zone\n"
            "$cpuTempObj = Get-WmiObject -Namespace root/WMI -Class MSAcpi_ThermalZoneTemperature -ErrorAction SilentlyContinue\n"
            "$cpuTemp = if ($cpuTempObj) { $cpuTempObj.CurrentTemperature } else { $null }\n"
            "$cpuTempC = if ($cpuTemp) { [math]::Round(($cpuTemp/10 - 273.15), 2) } else { 'N/A' }\n"
            "\n"
            "# GPU temperature (if requested and available)\n"
            "$gpuTempC = 'N/A'\n"
            "if ($IncludeGPU -eq $true) {\n"
            "    $gpuTempObj = Get-WmiObject -Namespace root/WMI -Class GPUTemperatures -ErrorAction SilentlyContinue\n"
            "    if ($gpuTempObj) {\n"
            "        $gpuTempC = [math]::Round($gpuTempObj.Temperature, 2)\n"
            "    }\n"
            "}\n"
            "\n"
            "# Memory usage\n"
            "$os = Get-WmiObject -Class Win32_OperatingSystem\n"
            "$totalMemMB = if ($os) { [math]::Round($os.TotalVisibleMemorySize / 1024, 2) } else { 'N/A' }\n"
            "$freeMemMB = if ($os) { [math]::Round($os.FreePhysicalMemory / 1024, 2) } else { 'N/A' }\n"
            "$usedMemMB = if ($totalMemMB -ne 'N/A' -and $freeMemMB -ne 'N/A') { $totalMemMB - $freeMemMB } else { 'N/A' }\n"
            "\n"
            "# CPU usage\n"
            "$cpuUsage = (Get-Counter '\\\\Processor(_Total)\\% Processor Time' -ErrorAction SilentlyContinue).CounterSamples.CookedValue\n"
            "\n"
            "# Disk activity\n"
            "$diskRead = (Get-Counter '\\\\PhysicalDisk(_Total)\\Disk Read Bytes/sec' -ErrorAction SilentlyContinue).CounterSamples.CookedValue\n"
            "$diskWrite = (Get-Counter '\\\\PhysicalDisk(_Total)\\Disk Write Bytes/sec' -ErrorAction SilentlyContinue).CounterSamples.CookedValue\n"
            "\n"
            "# Fan speed\n"
            "$fan = Get-WmiObject -Class Win32_Fan -ErrorAction SilentlyContinue\n"
            "$fanSpeed = if ($fan) { $fan.CurrentSpeed } else { 'N/A' }\n"
            "\n"
            "# Load average approximated by Processor Queue Length\n"
            "$load = (Get-Counter '\\\\System\\Processor Queue Length' -ErrorAction SilentlyContinue).CounterSamples.CookedValue\n"
            "\n"
            "# Process list\n"
            "$processes = Get-Process\n"
            "if ($FilterProcess) {\n"
            "    $processes = $processes | Where-Object { $_.ProcessName -like $FilterProcess }\n"
            "}\n"
            "$processList = $processes | Select-Object Name, Id, CPU, WorkingSet | ForEach-Object { $_ }\n"
            "\n"
            "# Driver stats\n"
            "$drivers = Get-WmiObject -Class Win32_SystemDriver | Select-Object Name, State\n"
            "$driverList = $drivers | ForEach-Object { $_ }\n"
            "\n"
            "# Build result object\n"
            "$result = @{\n"
            "    'CPU_Temp_C' = $cpuTempC\n"
            "    'GPU_Temp_C' = $gpuTempC\n"
            "    'Memory_Total_MB' = $totalMemMB\n"
            "    'Memory_Used_MB' = $usedMemMB\n"
            "    'Memory_Free_MB' = $freeMemMB\n"
            "    'CPU_Usage_Percent' = $cpuUsage\n"
            "    'Disk_Read_Bps' = $diskRead\n"
            "    'Disk_Write_Bps' = $diskWrite\n"
            "    'Fan_Speed_RPM' = $fanSpeed\n"
            "    'Load_Average' = $load\n"
            "    'Processes' = $processList\n"
            "    'Drivers' = $driverList\n"
            "}\n"
            "\n"
            "if ($OutputFormat -eq 'json') {\n"
            "    $result | ConvertTo-Json -Depth 4\n"
            "} else {\n"
            "    $result | Format-Table -AutoSize | Out-String\n"
            "}\n"
        )
        result = subprocess.run(
            ["powershell", "-Command", ps_script],
            capture_output=True, text=True, timeout=30
        )
        emit(True, result.stdout.strip())
    except Exception as e:
        emit(False, str(e))

if __name__ == "__main__":
    main()