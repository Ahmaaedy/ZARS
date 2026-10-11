import sys
sys.path.insert(0, '.')
from core.skillgen import generate_skill

description = 'Open following, brave in second half of secondary screen, notes in first half of second screen, and vscode on the main screen'
result = generate_skill(None, description, 'lets cook')
if result[0] and result[1]:
    print('Manifest generated successfully')
    print('Code generated successfully')
    print('\n=== Generated code ===')
    print(result[1][:500] + '...')
else:
    print(f'Failed: {result[0]}')
    print(f'Error: {result[1]}')