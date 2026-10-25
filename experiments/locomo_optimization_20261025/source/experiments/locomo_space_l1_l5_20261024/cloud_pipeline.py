"""Verified Space CSV import. No inferred write success and no L0 at test time."""
import argparse, collections, csv, hashlib, io, json, re
import httpx
from build_hierarchy import ROOT, RUN, read, write, space_config, now

PREFIX = RUN + '-csv'

def normalized(text):
    return re.sub(r'\s+', '', text or '')

def title(n):
    return f"{PREFIX} {n['conv']} {n['level']} {n['id']}"

def unwrap(response):
    response.raise_for_status()
    body = response.json()
    if body.get('code') != 200:
        raise RuntimeError(f"Space business failure: {body.get('code')} {body.get('msg')}")
    return body['data']

def inventory(client, base, records):
    rows = []
    page = 1
    while True:
        data = unwrap(client.get(base+'/memories', params={'keyword':PREFIX, 'page':page, 'pageSize':100}))
        rows.extend(data['list'])
        if len(rows) >= data['total']:
            break
        page += 1
    by_title = collections.defaultdict(list)
    for row in rows:
        by_title[row.get('title')].append(row)
    verified = {}
    issues = []
    for nid, n in records.items():
        matches = by_title[title(n)]
        if not matches:
            continue
        valid = [m for m in matches if m.get('level') == n['level']
                 and normalized(m.get('summary')) == normalized(n['content'])]
        if not valid:
            issues.append({'node_id':nid, 'reason':'level or full summary mismatch', 'cloud_ids':[m['id'] for m in matches]})
            continue
        m = valid[0]
        # Evidence is read back from Space, never substituted with local generated text.
        verified[nid] = {'cloud_id':m['id'], 'node_id':nid, 'conv':n['conv'], 'level':n['level'],
            'content':m['summary'], 'content_field':'summary', 'start':n['start'], 'end':n['end'],
            'cloud_content':m.get('content'), 'cloud_title':m.get('title'),
            'cloud_created_at':m.get('created_at'), 'verified_at':now(),
            'sha256':hashlib.sha256(m['summary'].encode()).hexdigest()}
    snapshot = {'run':RUN, 'read_at':now(), 'records':verified, 'issues':issues,
                'cloud_rows':len(rows), 'levels':dict(collections.Counter(x['level'] for x in verified.values()))}
    write(ROOT/'verified_cloud_memories.json', snapshot)
    return snapshot

def main(args):
    generated = read(ROOT/'generated_memories.json')
    plan = read(ROOT/'hierarchy_plan.json')
    records = generated['records']
    if args.command == 'upload' and set(records) != set(plan['nodes']):
        raise RuntimeError('Generation is incomplete; bulk upload has not started')
    cfg = space_config()
    with httpx.Client(timeout=300, trust_env=False, headers={'X-API-Key':cfg['KEY']}) as client:
        snapshot = inventory(client, cfg['SPACE'], records)
        if snapshot['issues']:
            raise RuntimeError('Existing batch contains invalid memories; inspect verified_cloud_memories.json')
        missing = [n for nid,n in records.items() if nid not in snapshot['records']]
        balance = unwrap(client.get(cfg['SPACE']+'/credits/balance'))['balance_units']
        estimate = {'remaining_nodes':len(missing), 'upload_units':len(missing)*40,
                    'recall_units':861*100, 'available_units':balance, 'unit_scale':100}
        write(ROOT/'cloud_cost_estimate.json', estimate)
        print(json.dumps({'verified':len(snapshot['records']), **estimate}), flush=True)
        if args.command == 'verify':
            return
        if balance < estimate['upload_units'] + estimate['recall_units']:
            raise RuntimeError('Insufficient Space credits for complete import and 861 recalls; no bulk writes performed')
        fields = ['content','level','memory_type','title','summary','importance_score','agent_id','subject']
        for offset in range(0, len(missing), args.batch_size):
            batch = missing[offset:offset+args.batch_size]
            text = io.StringIO()
            writer = csv.DictWriter(text, fieldnames=fields)
            writer.writeheader()
            for n in batch:
                writer.writerow({'content':n['content'], 'summary':n['content'], 'level':n['level'],
                    'memory_type':'memory','title':title(n), 'importance_score':0.5,'agent_id':'system','subject':'general'})
            # An ambiguous write is never automatically retried. Rerun inventories first.
            response = client.post(cfg['SPACE']+'/memories/import',
                files={'file':('hierarchy.csv',text.getvalue().encode('utf-8-sig'),'text/csv')})
            body = response.json()
            write(ROOT/f'import_batch_{offset:04d}.json',body)
            data = unwrap(response)
            if data.get('failed') or data.get('success') != len(batch):
                raise RuntimeError('Partial import; inspect batch response before resuming')
            snapshot = inventory(client, cfg['SPACE'], records)
            if snapshot['issues'] or any(n['id'] not in snapshot['records'] for n in batch):
                raise RuntimeError('Read-after-write verification failed')
            print('verified cloud',len(snapshot['records']), '/',len(records),flush=True)
        assert set(snapshot['records']) == set(plan['nodes'])
        print('ALL CLOUD LEVELS VERIFIED',json.dumps(snapshot['levels']),flush=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command',choices=['verify','upload'])
    parser.add_argument('--batch-size',type=int,default=10)
    main(parser.parse_args())
