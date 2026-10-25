import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
BACKEND=Path('F:/worktable/research/timem-aml')
before=(ROOT/'app.before.py').read_text(encoding='utf-8')
current=(BACKEND/'server/app.py').read_text(encoding='utf-8')
assert current==before,'app.py changed externally: merge before installing'
source=(BACKEND/'server/source_evidence.py').read_bytes()
experiment=json.loads((ROOT/'experiment.json').read_text(encoding='utf-8'))
assert hashlib.sha256(source).hexdigest()==experiment['source_sha256']
assert json.loads((ROOT/'summary.json').read_text(encoding='utf-8'))['complete']
(BACKEND/'server/app.py').write_bytes((ROOT/'app.staged.py').read_bytes())
print('Installed tested source-evidence Add/Search integration.')
