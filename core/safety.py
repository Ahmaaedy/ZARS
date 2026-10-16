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

# Power and mass app-closing are the only two things that stop for a y/n. They
# are reachable through a skill, a registered shell command, or a raw command
# the model invents, so both the entry id and the rendered command are checked.
CONFIRM_LIST = [
    (r"\bshutdown\b", "power action"),
    (r"\brestart-computer\b", "power action"),
    (r"\bstop-computer\b", "power action"),
    (r"\brundll32\b[^\n]*\blockworkstation\b", "locks the screen"),
    (r"\brundll32\b[^\n]*\bpowrprof\b", "power action"),
    (r"\bsetsuspendstate\b", "power action"),
]

CONFIRM_ENTRY_IDS = {
    "power_control": "power action",
    "close_non_background_apps": "closes every open app at once",
}

_CONFIRM_COMPILED = [(re.compile(p, re.IGNORECASE), reason) for p, reason in CONFIRM_LIST]


def check_blocked(command):
    for pattern, reason in _COMPILED:
        if pattern.search(command):
            return True, reason
    return False, None


def check_confirm(command, entry_id=None):
    """True when the command must be approved before it runs.

    `entry_id` catches skills whose danger is in what they do rather than in
    their arguments, like the cleanup skill that taskkills every window.
    """
    if entry_id in CONFIRM_ENTRY_IDS:
        return True, CONFIRM_ENTRY_IDS[entry_id]
    for pattern, reason in _CONFIRM_COMPILED:
        if pattern.search(command):
            return True, reason
    return False, None