
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from zars_sdk import build_parser, emit, manifest_for
import subprocess
from pathlib import Path

def main():
    ns = build_parser(manifest_for(__file__)).parse_args()
    try:
