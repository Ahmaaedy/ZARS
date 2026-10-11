import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zars_sdk import build_parser, emit, manifest_for

def main():
    ns = build_parser(manifest_for(__file__)).parse_args()
    dir_path = Path(ns.directory)
    file_count = 0
    if dir_path.exists():
        for item in dir_path.iterdir():
            if item.is_file():
                file_count += 1
    emit(True, {"count": file_count})
if __name__ == "__main__":
    main()