import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from zars_sdk import build_parser, emit, manifest_for

MODEL = "gemma4:12b"
MAX_TOKENS = 2048

MANIFEST_PROMPT = """You are generating a ZARS skill manifest (Windows, PowerShell, Python 3.13).
Output a JSON object with these keys:
- id (string, lowercase, max 30 chars, no spaces)
- desc (string, short description of what the skill does)
- params (list of {{name, type, required, desc}}) — type must be one of: string, number, integer, boolean
- keywords (list of strings for keyword matching)
- examples (list of example command strings)
- risk ("safe" or "confirm")

Task: {description}
Skill id: {skill_id}

Output raw JSON only. No markdown, no explanation."""

CODE_PROMPT = """Write a minimal skill.py for this ZARS manifest:
{manifest}

The code MUST:
1. Use build_parser(manifest_for(__file__)).parse_args() to get params
2. Use subprocess.run(["powershell", "-Command", ...]) with REAL PowerShell commands
3. Actually launch apps or perform the task — no placeholder strings
4. Return results via emit(True, data) or emit(False, str(e))

Boilerplate (copy exactly):
    import sys
    import subprocess
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from zars_sdk import build_parser, emit, manifest_for

    def main():
        ns = build_parser(manifest_for(__file__)).parse_args()
        try:
            result = subprocess.run(
                ["powershell", "-Command", "Start-Process notepad; Start-Sleep 1"],
                capture_output=True, text=True, timeout=30
            )
            emit(True, result.stdout.strip())
        except Exception as e:
            emit(False, str(e))

    if __name__ == "__main__":
        main()

Replace the PowerShell command with one that actually performs the task.
Access params via ns.param_name. Do NOT add extra functions or imports.
Task: {description}
Output raw Python code only. No markdown, no explanation."""


def _call_opencode(prompt):
    from ollama import Client
    client = Client(host="http://localhost:11434")
    response = client.chat(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.1, "num_ctx": 8192},
    )
    return response["message"]["content"]


def _extract_json(content):
    if content.startswith("```"):
        lines = content.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        content = "\n".join(lines)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    start = content.find("{")
    end = content.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(content[start:end])
        except json.JSONDecodeError:
            pass
    match = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return None


def _extract_code(content):
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        content = "\n".join(lines)
    return content.strip()


def _validate_manifest(manifest):
    errors = []
    if not manifest.get("id"):
        errors.append("missing 'id'")
    if not manifest.get("desc"):
        errors.append("missing 'desc'")
    if not isinstance(manifest.get("params", []), list):
        errors.append("'params' must be a list")
    for p in manifest.get("params", []):
        if not isinstance(p, dict) or "name" not in p:
            errors.append(f"param missing 'name': {p}")
        elif p.get("type", "string") not in {"string", "number", "integer", "boolean"}:
            errors.append(f"bad param type: {p.get('type')}")
    risk = manifest.get("risk")
    if risk and risk not in {"safe", "confirm"}:
        errors.append(f"bad risk: {risk}")
    return errors


def _gen_manifest(description, skill_id):
    content = _call_opencode(
        MANIFEST_PROMPT.format(description=description, skill_id=skill_id)
    )
    if not content or not content.strip():
        return None, "model returned empty response for manifest"
    data = _extract_json(content.strip())
    if data is None:
        return None, f"could not parse manifest JSON: {content[:300]}"
    errors = _validate_manifest(data)
    if errors:
        return None, f"manifest validation failed: {'; '.join(errors)}"
    data["id"] = skill_id
    data["handler"] = {"type": "skill"}
    if "risk" not in data:
        data["risk"] = "safe"
    return data, None


def _gen_code(manifest, description):
    content = _call_opencode(
        CODE_PROMPT.format(
            manifest=json.dumps(manifest, indent=2),
            description=description,
        )
    )
    if not content or not content.strip():
        return None, "model returned empty response for code"
    code = _extract_code(content)
    if "emit" not in code:
        return None, "generated code missing emit() call"
    required = ["manifest_for", "build_parser"]
    missing = [r for r in required if r not in code]
    if missing:
        header = (
            "import sys\nimport subprocess\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parents[2]))\n"
            "from zars_sdk import build_parser, emit, manifest_for\n\n"
        )
        code = header + code
    lines = []
    seen_imports = set()
    for line in code.split("\n"):
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            if stripped in seen_imports:
                continue
            seen_imports.add(stripped)
        lines.append(line)
    code = "\n".join(lines)
    return code, None


def generate_skill(config, description, skill_id=None):
    try:
        if skill_id:
            trigger = skill_id
        else:
            trigger = re.sub(r"[^a-z0-9]+", "_", description.lower().strip())[:30].strip("_")

        manifest, err = _gen_manifest(description, trigger)
        if manifest is None:
            return None, err

        code, err = _gen_code(manifest, description)
        if code is None:
            return None, err

        return manifest, code
    except Exception as e:
        return None, f"skill generation failed: {e}"


def save_skill(skill_name, skill_json, skill_py, root=None):
    root = root or Path(__file__).resolve().parent.parent
    skill_dir = root / "skills" / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.json").write_text(json.dumps(skill_json, indent=2, ensure_ascii=False), encoding="utf-8")
    (skill_dir / "skill.py").write_text(skill_py, encoding="utf-8")
    return str(skill_dir / "skill.py")
