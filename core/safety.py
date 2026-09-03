import re

BLOCKLIST = [
    (r"\bdiskpart\b", "diskpart is blocked"),
    (r"\bformat(-volume|\s+[a-z]:|\s*/)", "format is blocked"),
    (r"\b(clear|initialize)-disk\b", "disk clearing is blocked"),
    (r"\bdel\s+/[sq]", "recursive/quiet delete is blocked"),
    (r"\berase\s+/[sq]", "recursive/quiet delete is blocked"),
    (r"\brd(r)?\s+/s\b", "recursive dir removal is blocked"),
    (r"\brmdir\s+/s\b", "recursive dir removal is blocked"),
    (r"\bremove-item\b.*-recurse", "Remove-Item -Recurse is blocked"),
    (r"\breg\s+(delete|add|import|restore|save|export|load|unload)\b", "registry edits are blocked"),
    (r"\b(shutdown|restart-computer|stop-computer)\b", "power actions are blocked"),
    (r"\b(stop|restart)-service\b", "service control is blocked"),
    (r"\bcipher\s+/w\b", "cipher wipe is blocked"),
    (r"\bvssadmin\s+delete\b", "shadow copy deletion is blocked"),
    (r"\bbcdedit\b", "boot config edits are blocked"),
    (r"\bmkfs\b", "mkfs is blocked"),
    (r"\bdd\s+if=", "raw disk writes are blocked"),
    (r"\bnet\s+user\b", "user management is blocked"),
    (r"\bset-executionpolicy\b", "execution policy changes are blocked"),
    (r"\bschtasks\s+/create\b", "scheduled task creation is blocked"),
]

_COMPILED = [(re.compile(p, re.IGNORECASE), reason) for p, reason in BLOCKLIST]


def check_blocked(command):
    for pattern, reason in _COMPILED:
        if pattern.search(command):
            return True, reason
    return False, None