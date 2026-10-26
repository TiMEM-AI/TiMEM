"""Read-only structural audit; evaluation questions are not used."""
import collections, hashlib, json, sqlite3
from build_hierarchy import ROOT, SOURCE, read, write, source_hash, now

def main():
    plan = read(ROOT/'hierarchy_plan.json')
    records = read(ROOT/'generated_memories.json')['records']
    nodes = plan['nodes']
    db = sqlite3.connect('file:'+SOURCE.as_posix()+'?mode=ro',uri=True)
    originals = {str(row[0]) for row in db.execute('SELECT id FROM source_turns')}
    db.close()
    source_ids = [str(s) for n in nodes.values() for s in n['source_ids']]
    issues = []
    if set(source_ids)!=originals:issues.append('L0 coverage mismatch')
    if len(source_ids)!=len(set(source_ids)):issues.append('Duplicate L0 assignment')
    for nid,n in nodes.items():
        for child_id in n['child_ids']:
            child = nodes[child_id]
            if child['conv']!=n['conv']:issues.append('Cross-conversation child '+nid)
            if not (n['start']<=child['start']<=child['end']<=n['end']):issues.append('Noncontained child '+nid)
            if int(n['level'][1:])!=int(child['level'][1:])+1:issues.append('Invalid tier edge '+nid)
    if plan['source_sha256']!=source_hash():issues.append('Source snapshot changed')
    missing = sorted(set(nodes)-set(records))
    for nid,n in records.items():
        if len(n['content'])<25:issues.append('Trivial content '+nid)
        if '2026-10-23' in n['content'] or '2026-10-24' in n['content']:
            issues.append('Current evaluation date leaked into historical memory '+nid)
    by_conv = collections.defaultdict(collections.Counter)
    for n in records.values():by_conv[n['conv']][n['level']]+=1
    out = {'audited_at':now(),'source_turns':len(originals),'source_assignment_count':len(source_ids),
        'source_sha256':source_hash(),'planned':len(nodes),'generated':len(records),
        'missing':missing,'issues':issues,'complete':not issues and not missing,
        'levels':dict(collections.Counter(n['level'] for n in records.values())),
        'per_conversation':{k:dict(v) for k,v in by_conv.items()},
        'generation_methods':dict(collections.Counter(n['generation_method'] for n in records.values())),
        'content_note':'Single-child parents preserve the child verbatim; retrieval deduplicates identical content.',
        'method':'Local chronological grouping and external DeepSeek consolidation; not native Space consolidation.'}
    write(ROOT/'hierarchy_audit.json',out)
    print(json.dumps({k:v for k,v in out.items() if k not in ['missing','per_conversation']},ensure_ascii=False))
    if issues:raise RuntimeError('Hierarchy audit failed')
    if missing:raise RuntimeError('Generation not complete')

if __name__=='__main__':main()
