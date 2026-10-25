from pathlib import Path
import difflib, hashlib, json
ROOT=Path(__file__).resolve().parent
BACKEND=Path('F:/worktable/research/timem-aml')
p=BACKEND/'server/app.py'
old=p.read_text(encoding='utf-8')
backup=ROOT/'app.before.py'
if not backup.exists(): backup.write_text(old,encoding='utf-8')
assert old==backup.read_text(encoding='utf-8')
new=old.replace('    store = idempotency\n','    store = idempotency\n    from server.source_evidence import SourceEvidenceStore, ground_evidence\n    sources = SourceEvidenceStore()\n',1)
# Use the module import consistently.
new=new.replace('''        try:
            import asyncio

            updated = await asyncio.to_thread(stamp_temporal, body.user_id)''','''        try:
            updated = await asyncio.to_thread(stamp_temporal, body.user_id)''',1)
needle='''        if store is not None:
            store.put(body.request_id, payload)'''
replacement='''        await asyncio.to_thread(sources.put, body.user_id, body.session_id, body.request_id, body.messages)
        if store is not None:
            store.put(body.request_id, payload)'''
assert needle in new; new=new.replace(needle,replacement,1)
needle='''        rows = await asyncio.to_thread(filter_owned, body.user_id, rows)
        data = []'''
replacement='''        rows = await asyncio.to_thread(filter_owned, body.user_id, rows)
        rows = await asyncio.to_thread(ground_evidence, body.user_id, body.query, rows, body.top_k, store=sources)
        data = []'''
assert needle in new; new=new.replace(needle,replacement,1)
(ROOT/'app.staged.py').write_text(new,encoding='utf-8')
(ROOT/'app.diff').write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='server/app.py (before)',tofile='server/app.py (after)')),encoding='utf-8')
print('App staged, prior SHA256',hashlib.sha256(old.encode()).hexdigest())
