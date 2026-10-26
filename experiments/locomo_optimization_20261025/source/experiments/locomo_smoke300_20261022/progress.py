import collections,json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
stats={}; rows={}
for arm in ('baseline','variant'):
    directory=ROOT/arm
    path=directory/'aml_score.json'
    if not path.exists(): path=directory/'aml_score.partial.json'
    try: data=json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError,json.JSONDecodeError): data={'results':[]}
    rows[arm]={r['question_id']:r for r in data['results']}
    stats[arm]={'n':len(rows[arm]),'correct':sum(bool(r['correct']) for r in rows[arm].values())}
common=rows['baseline'].keys() & rows['variant'].keys()
stats['paired_n']=len(common)
stats['wrong_to_right']=sum(not rows['baseline'][k]['correct'] and rows['variant'][k]['correct'] for k in common)
stats['right_to_wrong']=sum(rows['baseline'][k]['correct'] and not rows['variant'][k]['correct'] for k in common)
print(json.dumps(stats))
