import sys
sys.path.insert(0, '.')
from core.skillgen import generate_skill

description = 'Open following, brave in second half of secondary screen, notes in first half of second screen, and vscode on the main screen'
result = generate_skill(None, description, 'lets cook')
if result[0] and result[1]:
    with open('test_skill.py', 'w') as f:
        f.write(result[1])
    print('Skill written to test_skill.py')
    print('\n=== First 10 lines ===')
    with open('test_skill.py') as f:
        for i, line in enumerate(f, 1):
            if i <= 10:
                print(f'{i:3d}: {line}', end='')
            else:
                break
else:
    print(f'Failed: {result[0]}')
    print(f'Error: {result[1]}')