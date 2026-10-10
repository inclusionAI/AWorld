import json
from pathlib import Path

cursor_path = Path('tmp/self-evolve-v19-events-cursor.json')
cursor = json.loads(cursor_path.read_text())
lines = Path('tmp/self-evolve-acceptance-20260918-v19.log').read_text().splitlines()
for line in lines[cursor['line']:]:
    if 'still running' not in line:
        print(line)
cursor['line'] = len(lines)
for path in sorted(Path('.aworld/self_evolve').glob('campaign-9463c6a408362f33e443-cycle-*/report.json')):
    if str(path) in cursor['reports']:
        continue
    report = json.loads(path.read_text())
    print(json.dumps({'run': path.parent.name, 'status': report.get('status'),
        'selected': report.get('selected_candidate_id'),
        'focus': report.get('repair_focus_candidate_id'),
        'failed_gates': [(g.get('gate_name'), g.get('reason')) for g in report.get('gate_results', []) if not g.get('passed')]}, ensure_ascii=False))
    cursor['reports'].append(str(path))
cursor_path.write_text(json.dumps(cursor) + '\n')
