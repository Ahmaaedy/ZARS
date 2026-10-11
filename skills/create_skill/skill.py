import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zars_sdk import build_parser, emit, manifest_for
from core.skillgen import generate_skill, save_skill
from core.config import Config


class StderrConsole:
    def print(self, *args, **kwargs):
        msg = " ".join(str(a) for a in args)
        import re
        msg = re.sub(r"\[.*?\]", "", msg)
        print(msg, file=sys.stderr)


def main():
    ns = build_parser(manifest_for(__file__)).parse_args()
    config = Config()

    try:
        manifest, code = generate_skill(config, ns.description, ns.skill_id, console=StderrConsole())
        if manifest is None:
            emit(False, f"generation failed: {code}")
            return

        path = save_skill(manifest["id"], manifest, code)
        emit(True, {
            "skill_id": manifest["id"],
            "path": path,
            "desc": manifest.get("desc", ""),
        })
    except Exception as e:
        emit(False, str(e))


if __name__ == "__main__":
    main()
