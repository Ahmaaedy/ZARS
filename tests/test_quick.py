"""Quick test of single skill generation"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.skillgen import generate_skill
from core.config import Config

config = Config()
print("Testing single skill generation...")
manifest, code = generate_skill(config, "opens notepad", "test_quick")
print(f"Manifest: {manifest is not None}")
print(f"Code: {code is not None}")
if manifest:
    print(f"ID: {manifest.get('id')}")
    print(f"Desc: {manifest.get('desc')}")
