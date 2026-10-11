import sys
sys.path.insert(0, '.')
from core.skillgen import generate_skill

description = 'Open following, brave in second half of secondary screen, notes in first half of second screen, and vscode on the main screen'
result = generate_skill(None, description, 'lets cook')
if result[0] and result[1]:
    skill_dir = 'skills/lets cook'
    import json
    from pathlib import Path
    skill_dir_path = Path(skill_dir)
    skill_dir_path.mkdir(parents=True, exist_ok=True)
    manifest_file = skill_dir_path / 'skill.json'
    
    # Write manifest
    manifest = {
        "id": "lets cook",
        "desc": "Open applications across multiple screens: Brave on secondary screen, notes on second screen, VSCode on main",
        "params": [
            {
                "name": "app_name",
                "type": "string",
                "required": True,
                "desc": "Name of the application to open"
            },
            {
                "name": "screen_region",
                "type": "string",
                "required": False,
                "desc": "Screen area: main, secondary, or specific half"
            }
        ],
        "keywords": [
            "open",
            "application",
            "window",
            "screen",
            "brave",
            "vscode",
            "notes"
        ],
        "examples": [
            "lets cook with 'following' on brave, 'notes' on second screen, 'vscode' on main",
            "lets cook to open brave, notes, and vscode on respective screens"
        ],
        "risk": "safe",
        "handler": {
            "type": "skill"
        }
    }
    manifest_file.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    
    # Write skill.py
    skill_file = skill_dir_path / 'skill.py'
    skill_file.write_text(result[1], encoding='utf-8')
    
    print('Generated skill.py:')
    print('-' * 60)
    print(result[1])
else:
    print(f'Failed: {result[0]}')
    print(f'Error: {result[1]}')