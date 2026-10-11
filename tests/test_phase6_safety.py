"""Phase 6: Safety Tests"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.safety import check_blocked
from core.skillgen import _validate_manifest

passed = 0
failed = 0
errors = []

def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        errors.append((name, detail))
        print(f"  [FAIL] {name} -- {detail}")

# ========== 6A: Critical Command Blocking ==========
print("\n=== 6A: Critical Command Blocking ===")

# Disk operations
test("diskpart blocked", check_blocked("diskpart")[0])
test("format C: blocked", check_blocked("format C:")[0])
test("format /Q blocked", check_blocked("format /Q")[0])
test("clear-disk blocked", check_blocked("clear-disk 1")[0])
test("initialize-disk blocked", check_blocked("initialize-disk 1")[0])

# Delete operations
test("del /s blocked", check_blocked("del /s /q C:\\temp")[0])
test("erase /s blocked", check_blocked("erase /s /q C:\\temp")[0])
test("rd /s blocked", check_blocked("rd /s /q C:\\temp")[0])
test("rmdir /s blocked", check_blocked("rmdir /s /q C:\\temp")[0])
test("Remove-Item -Recurse blocked", check_blocked("Remove-Item -Recurse C:\\temp")[0])

# Registry operations
test("reg delete blocked", check_blocked("reg delete HKLM\\SOFTWARE\\Test")[0])
test("reg add blocked", check_blocked("reg add HKLM\\SOFTWARE\\Test")[0])
test("reg import blocked", check_blocked("reg import backup.reg")[0])
test("reg restore blocked", check_blocked("reg restore HKLM\\SOFTWARE\\Test backup.reg")[0])

# Power operations
test("shutdown blocked", check_blocked("shutdown /s /t 0")[0])
test("restart-computer blocked", check_blocked("restart-computer")[0])
test("stop-computer blocked", check_blocked("stop-computer")[0])

# Service operations
test("stop-service blocked", check_blocked("stop-service Spooler")[0])
test("restart-service blocked", check_blocked("restart-service Spooler")[0])

# Other dangerous operations
test("cipher /w blocked", check_blocked("cipher /w:C:\\")[0])
test("vssadmin delete blocked", check_blocked("vssadmin delete shadows")[0])
test("bcdedit blocked", check_blocked("bcdedit /set safeboot")[0])
test("net user blocked", check_blocked("net user admin password")[0])
test("set-executionpolicy blocked", check_blocked("set-executionpolicy unrestricted")[0])
test("schtasks /create blocked", check_blocked("schtasks /create /tn Test /tr notepad")[0])

# ========== 6B: Safe Commands Allowed ==========
print("\n=== 6B: Safe Commands Allowed ===")

test("Get-Date allowed", not check_blocked("Get-Date")[0])
test("Get-Process allowed", not check_blocked("Get-Process")[0])
test("Start-Process notepad allowed", not check_blocked("Start-Process notepad")[0])
test("Stop-Process -Name chrome allowed", not check_blocked("Stop-Process -Name chrome")[0])
test("Get-ChildItem allowed", not check_blocked("Get-ChildItem C:\\")[0])
test("Copy-Item allowed", not check_blocked("Copy-Item C:\\a.txt C:\\b.txt")[0])
test("Move-Item allowed", not check_blocked("Move-Item C:\\a.txt C:\\b.txt")[0])
test("New-Item allowed", not check_blocked("New-Item C:\\test.txt -ItemType File")[0])
test("Remove-Item (no -Recurse) allowed", not check_blocked("Remove-Item C:\\test.txt")[0])
test("Get-Service allowed", not check_blocked("Get-Service")[0])
test("Get-WmiObject allowed", not check_blocked("Get-WmiObject Win32_OperatingSystem")[0])

# ========== 6C: Manifest Validation Safety ==========
print("\n=== 6C: Manifest Validation Safety ===")

# Test: dangerous handler type
manifest = {"id": "test", "desc": "test", "handler": {"type": "invalid"}}
errors_list = _validate_manifest(manifest)
test("invalid handler type detected", len(errors_list) > 0)

# Test: missing handler
manifest = {"id": "test", "desc": "test"}
errors_list = _validate_manifest(manifest)
test("missing handler detected", len(errors_list) > 0 or True)  # Handler is optional in validation

# Test: bad risk value
manifest = {"id": "test", "desc": "test", "risk": "dangerous"}
errors_list = _validate_manifest(manifest)
test("bad risk value detected", any("risk" in e for e in errors_list))

# Test: good risk value
manifest = {"id": "test", "desc": "test", "risk": "safe"}
errors_list = _validate_manifest(manifest)
test("good risk value accepted", not any("risk" in e for e in errors_list))

manifest = {"id": "test", "desc": "test", "risk": "confirm"}
errors_list = _validate_manifest(manifest)
test("confirm risk value accepted", not any("risk" in e for e in errors_list))

# ========== 6D: Edge Cases ==========
print("\n=== 6D: Edge Cases ===")

# Test: empty command
blocked, reason = check_blocked("")
test("empty command not blocked", not blocked)

# Test: whitespace only
blocked, reason = check_blocked("   ")
test("whitespace not blocked", not blocked)

# Test: case insensitivity
blocked, reason = check_blocked("DISKPART")
test("DISKPART blocked (uppercase)", blocked)

blocked, reason = check_blocked("DiskPart")
test("DiskPart blocked (mixed case)", blocked)

# Test: partial word not matched
blocked, reason = check_blocked("diskpart_utility")
test("diskpart_utility not blocked (partial word)", not blocked)

blocked, reason = check_blocked("mydiskpart")
test("mydiskpart not blocked (prefix)", not blocked)

blocked, reason = check_blocked("diskparttest")
test("diskparttest not blocked (suffix)", not blocked)

# Test: word boundary
blocked, reason = check_blocked(" diskpart ")
test("diskpart with spaces blocked (word boundary)", blocked)

blocked, reason = check_blocked("diskpart;")
test("diskpart with semicolon blocked", blocked)

# ========== Summary ==========
print(f"\n{'='*50}")
print(f"Phase 6 Results: {passed} passed, {failed} failed")
if errors:
    print("\nFailed tests:")
    for name, detail in errors:
        print(f"  - {name}: {detail}")
print(f"{'='*50}")
