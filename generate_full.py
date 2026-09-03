import sys
sys.path.insert(0, '.')
from core.skillgen import generate_skill

description = 'Open following, brave in second half of secondary screen, notes in first half of second screen, and vscode on the main screen'
result = generate_skill(None, description, 'lets cook')
if result[0] and result[1]:
    # Save manifest to file
    import json
    from pathlib import Path
    skill_dir = Path('skills') / 'lets cook'
    skill_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = skill_dir / 'skill.json'
    
    # We need to use the manifest data for saving
    # Let's read the current generated skill to extract manifest info
    import re
    # Find the JSON in the original manifest
    manifest_content = result[1].split('\n\n', 1)[0] if '\n\n' in result[1] else result[1]
    try:
        manifest = json.loads(manifest_content)
    except:
        print("Could not parse manifest from generated code")
        # Create a basic manifest
        manifest = {
            "id": "lets cook",
            "desc": "Open applications across multiple screens",
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
    
    # Save manifest
    manifest_file.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"Manifest written to {manifest_file}")
    
    # Save skill.py
    skill_file = skill_dir / 'skill.py'
    skill_file.write_text(result[1], encoding='utf-8')
    print(f"Skill written to {skill_file}")
    
    print('\n=== Generated skill.py (first 30 lines) ===')
    with open(skill_file) as f:
        for i, line in enumerate(f, 1):
            if i <= 30:
                print(f'{i:3d}: {line}', end='')
            else:
                break
                
    print('\n=== Manifest (first 30 lines) ===')
    with open(manifest_file) as f:
        for i, line in enumerate(f, 1):
            if i <= 30:
                print(f'{i:3d}: {line}', end='')
            else:
                break
else:
    print(f'Failed: {result[0]}')
    print(f'Error: {result[1]}')