"""Inherited evidence, hierarchical navigation, and a controlled L1/L2 arm."""
import collections, dataclasses, functools, hashlib, json, math, re, sqlite3, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent
PRIOR=ROOT.parent/'locomo_upper_w0_20261024'
sys.path.insert(0,str(PRIOR))
import upper_w0 as previous
from upper_w0 import read,write,now,sha,canonical,norm,a0,week,format_memories,MODEL,_score_endpoints,W0

OLD=previous.PREVIOUS
STOP=set('a an the is are was were be been being do does did have has had of to in on at for from with by and or as it its this that these those i you he she they we my your his her their our what which who when where why how would could should can will likely some any about both between said says told asked'.split())
RULES={'schema':'inherited-evidence-navigation-v1','retrieval_policy':'conditional-navigation-v2','max_windows':24,'protected_flat_windows':20,
    'upper_extra_windows':4,'context_radius':2,'opening_facts':2,'rrf_k':60,
    'route_min_dice':0.38,'route_link_limit':2,'route_link_margin':0.03,
    'leaf_candidates':400,'route_candidates':200,'annotation_budget':8000,
    'formatter':'original W0 format_memories','runtime_routing_uses_gold':False,
    'development':'fixed prior 40-question smoke; policy frozen before full comparison',
    'upper_guard':'compositional query, informative query token, new observation date, verified child link'}

def compositional_query(query):
    if re.match(r'\s*(?:when\b|(?:what|which)\s+(?:date|day|month|year|week)\b|how\s+long\b)',query,re.I):return False
    return bool(re.search(r'\b(?:both|common|together|compare|comparison|besides|other|all|list|and|why)\b'
        r'|\b(?:what\s+(?:are|kinds?|types?|\w+s)|which\b|how\s+many\b)',query,re.I))

def clean_body(text):
    text=re.sub(r'^\[\d{4}-\d{2}-\d{2}[^\]]*\]\s*','',text).strip()
    return re.sub(r'^(?:On\s+)?\d{4}-\d{2}-\d{2}T[\d:+.Z-]+[,:]?\s*','',text).strip()

def words(text):
    return set(t for t in re.findall(r'[a-z0-9]+',clean_body(text).lower()) if t not in STOP and len(t)>1)

def dice(left,right):
    return 2*len(left&right)/max(1,len(left)+len(right))

def original_annotations():
    plan=read(OLD/'hierarchy_plan.json')
    db=sqlite3.connect('file:'+Path(plan['source_path']).as_posix()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    by_date=collections.defaultdict(list);source_count=0
    for r in db.execute('SELECT * FROM source_turns'):
        r=dict(r);source_count+=1;sid=r['id']
        by_date.setdefault((r['user_id'].split(':')[-1],r['observed_at']),[])
        st=a0.SourceTurn(sid,r['user_id'],r['role'],r['content'],r['observed_at'])
        entries=[]
        for m in a0.parse_time_mentions(st):
            if m.status=='resolved' and m.normalized_value:
                entries.append({'kind':'calendar','data':dataclasses.asdict(m),'line':a0._annotation_line(m)})
        for m in week.parse_week_mentions({'source_id':sid,**r},with_range=False):
            entries.append({'kind':'week','data':dataclasses.asdict(m),'line':week._line(m)})
        for e in entries:
            start,end=e['data']['span'];assert r['content'][start:end]==e['data']['raw_phrase']
            e['source_span_verified']=True
        by_date[(r['user_id'].split(':')[-1],r['observed_at'])].extend(entries)
    db.close()
    return by_date,source_count

def construct():
    ROOT.mkdir(exist_ok=True)
    old=read(OLD/'generated_memories.json')['records']
    upper=read(PRIOR/'memories.json')['records']
    dated,source_count=original_annotations()
    facts={};groups=collections.OrderedDict();nodes={};fact_keys={}
    # L1 facts remain detailed, L2 supplies additional consolidated statements.
    for level in ['L1','L2']:
        ns=sorted((n for n in old.values() if n['level']==level),key=lambda n:(n['conv'],n['start'],n['key']))
        for n in ns:
            assert n['start']==n['end']
            assert (n['conv'],n['start']) in dated
            gid=n['conv']+':L1-session:'+n['start'] if level=='L1' else n['id']
            sequence=groups.setdefault(gid,[]);ids=[]
            for unit in previous.split_units(n,[n['start']]):
                assert unit['observed_at'] in [None,n['start']]
                body=clean_body(unit['text']);key=(n['conv'],n['start'],norm(body))
                fid=fact_keys.setdefault(key,'fact:'+sha(canonical(key))[:32])
                if fid not in facts:
                    allowed={k:previous.eligible_phrases(body,k) for k in ['calendar','week']}
                    annotations=[];seen=set()
                    for e in dated[(n['conv'],n['start'])]:
                        phrase=e['data']['raw_phrase'];k=(e['kind'],norm(phrase),e['data'].get('normalized_value',e['data'].get('anchored_value')))
                        if norm(phrase) not in allowed[e['kind']] or k in seen:continue
                        if re.search(r'\b(?:about|around|roughly|approximately|nearly|almost|early|late|mid|sometime)\s+'+re.escape(phrase),body,re.I):continue
                        seen.add(k);annotations.append(e)
                    facts[fid]={'id':fid,'conv':n['conv'],'observed_at':n['start'],'text':body,
                        'origins':[],'context_group':gid,'annotations':annotations,
                        'time_authority':'single-observation L1/L2 source node, not any upper window'}
                facts[fid]['origins'].append({'node_id':n['id'],'level':level})
                if fid not in sequence:sequence.append(fid)
                if fid not in ids:ids.append(fid)
            nodes[n['id']]={'id':n['id'],'conv':n['conv'],'level':level,'child_ids':n['child_ids'],
                'fact_ids':ids,'summary':n['content'],'start':n['start'],'end':n['end']}
    @functools.lru_cache(None)
    def inherited(nid):
        n=old[nid]
        own=nodes[nid]['fact_ids'] if n['level'] in ['L1','L2'] else []
        # L2 also inherits any distinct detailed L1 fact that its summary omitted.
        return tuple(dict.fromkeys(own+[fid for c in n['child_ids'] for fid in inherited(c)]))
    tokens={fid:words(f['text']) for fid,f in facts.items()}
    routes={};audit=collections.Counter();conflicts=[];linked_facts=set()
    for level in ['L2','L3','L4','L5']:
        for n in old.values():
            if n['level']!=level:continue
            ids=list(inherited(n['id']))
            if level=='L2':nodes[n['id']]['fact_ids']=ids;continue
            nodes[n['id']]={'id':n['id'],'conv':n['conv'],'level':level,'child_ids':n['child_ids'],
                'fact_ids':ids,'summary':n['content'],'start':n['start'],'end':n['end'],
                'policy':'summary routes to inherited child facts; summary dates never overwrite child dates'}
            for unit in upper[n['id']]['fact_units']:
                query_tokens=words(unit['text'])
                scores=[(dice(query_tokens,tokens[fid]),facts[fid]['observed_at']==unit['observed_at'],fid) for fid in ids]
                scores.sort(reverse=True)
                audit['upper_route_units']+=1
                best=scores[0][0] if scores else 0
                links=[{'fact_id':fid,'similarity':score} for score,_,fid in scores
                    if score>=RULES['route_min_dice'] and score>=best-RULES['route_link_margin']][:RULES['route_link_limit']]
                if links:audit['linked_route_units']+=1
                else:audit['unlinked_route_units']+=1
                if scores and best>=0.85 and not scores[0][1]:
                    same=max((s for s,same,_ in scores if same),default=0)
                    if best-same>=0.15:
                        audit['strong_date_disagreement_candidates']+=1
                        if len(conflicts)<30:conflicts.append({'node':n['id'],'unit':unit['id'],'upper_observed_at':unit['observed_at'],
                            'child_observed_at':facts[scores[0][2]]['observed_at'],'similarity':best,'same_date_best_similarity':same})
                for link in links:
                    if link['similarity']>=0.65:linked_facts.add(link['fact_id'])
                routes[unit['id']]={'id':unit['id'],'conv':n['conv'],'level':level,'node_id':n['id'],
                    'text':clean_body(unit['text']),'legacy_observed_at':unit['observed_at'],'links':links}
    for n in nodes.values():
        assert all(facts[fid]['conv']==n['conv'] for fid in n['fact_ids'])
        assert all(n['start']<=facts[fid]['observed_at']<=n['end'] for fid in n['fact_ids'])
    store={'schema':RULES['schema'],'created_at':now(),'facts':facts,'context_groups':groups,'nodes':nodes,'routes':routes,
        'source_policy':'No original turn content copied; original data used only to verify short calendar annotations during construction'}
    write(ROOT/'store.json',store);write(ROOT/'rules.json',RULES)
    audit.update({'canonical_lower_facts':len(facts),'nodes':len(nodes),'raw_source_turns_verified':source_count,
        'facts_with_strong_lexical_upper_link':len(linked_facts),'facts_without_strong_lexical_upper_link':len(facts)-len(linked_facts)})
    write(ROOT/'storage_audit.json',{'created_at':now(),'passed':True,'counts':dict(audit),'date_disagreement_examples':conflicts,
        'limits':'Link coverage and date disagreement are lexical proxies, not proof of semantic information loss or wrong dates.',
        'lower_source_snapshot_unchanged':True,'questions_and_gold_used_at_construction':False,
        'parent_fact_inheritance_complete':True,'cross_conversation_edges':0})
    database=ROOT/'index.sqlite'
    db=sqlite3.connect(database)
    # Rebuild only our own named index, never external databases.
    db.executescript('DROP TABLE IF EXISTS facts; DROP TABLE IF EXISTS fact_fts; DROP TABLE IF EXISTS routes; DROP TABLE IF EXISTS route_fts;'
        'CREATE TABLE facts(id TEXT,conv TEXT); CREATE VIRTUAL TABLE fact_fts USING fts5(content,tokenize="porter unicode61");'
        'CREATE TABLE routes(id TEXT,conv TEXT); CREATE VIRTUAL TABLE route_fts USING fts5(content,tokenize="porter unicode61");')
    for f in facts.values():
        cursor=db.execute('INSERT INTO facts VALUES(?,?)',(f['id'],f['conv']))
        db.execute('INSERT INTO fact_fts(rowid,content) VALUES(?,?)',(cursor.lastrowid,f['text']))
    for route in routes.values():
        if not route['links']:continue
        cursor=db.execute('INSERT INTO routes VALUES(?,?)',(route['id'],route['conv']))
        db.execute('INSERT INTO route_fts(rowid,content) VALUES(?,?)',(cursor.lastrowid,route['text']))
    db.commit();db.close()
    print('STORAGE',json.dumps(dict(audit)),flush=True)

class Retriever:
    def __init__(self):
        self.store=read(ROOT/'store.json');self.facts=self.store['facts'];self.routes=self.store['routes']
        self.db=sqlite3.connect('file:'+(ROOT/'index.sqlite').as_posix()+'?mode=ro',uri=True)
        self.positions={}
        self.token_df=collections.defaultdict(collections.Counter);self.conv_size=collections.Counter()
        for fact in self.facts.values():
            self.token_df[fact['conv']].update(words(fact['text']));self.conv_size[fact['conv']]+=1
        for gid,ids in self.store['context_groups'].items():
            for index,fid in enumerate(ids):
                if self.facts[fid]['context_group']==gid:self.positions[fid]=(gid,index)
    def search(self,conv,query,kind):
        terms=list(dict.fromkeys(t.lower() for t in re.findall(r'\w+',query) if t.lower() not in STOP and len(t)>1))[:32]
        if not terms:return []
        expression=' OR '.join('"'+t+'"' for t in terms)
        table='facts' if kind=='fact' else 'routes';fts=kind+'_fts'
        limit=RULES['leaf_candidates'] if kind=='fact' else RULES['route_candidates']
        rows=self.db.execute(f'SELECT r.id,bm25({fts}) AS rank FROM {fts} JOIN {table} r ON r.rowid={fts}.rowid '
            f'WHERE {fts} MATCH ? AND r.conv=? ORDER BY rank,r.rowid LIMIT ?',(expression,conv,limit)).fetchall()
        return [{'id':row[0],'score':-row[1]} for row in rows]
    def window(self,fid):
        gid,index=self.positions[fid];ids=self.store['context_groups'][gid]
        indices=list(range(max(0,index-2),min(len(ids),index+3)))
        if index>3:indices=list(range(min(2,len(ids))))+indices
        return list(dict.fromkeys(ids[i] for i in sorted(set(indices))))
    def windows(self,conv,query,all_levels):
        hits=self.search(conv,query,'fact');covered=set();flat=[]
        for hit in hits:
            fid=hit['id']
            if fid in covered:continue
            ids=[x for x in self.window(fid) if x not in covered]
            flat.append({'center':fid,'fact_ids':ids,'channel':'lower','route_ids':[],'route_levels':[]})
            covered.update(ids)
            if len(flat)==RULES['max_windows']:break
        if not all_levels:return flat,{'consulted_levels':['L1','L2'],'route_hits':0,'upper_windows':0}
        route_hits=self.search(conv,query,'route');rank={h['id']:i+1 for i,h in enumerate(hits)}
        eligible=compositional_query(query)
        if not eligible:
            return flat,{'consulted_levels':['L1','L2','L3','L4','L5'],'route_hits':len(route_hits),'upper_windows':0,
                'guard':'simple/direct-time query; lower evidence unchanged'}
        informative={t for t in words(query) if self.token_df[conv][t]/max(1,self.conv_size[conv])<=0.2}
        informative-=set('kind kinds type types things other all both common together list compare comparison are why'.split())
        candidates=collections.defaultdict(dict);route_proof=collections.defaultdict(list)
        for index,hit in enumerate(route_hits):
            route=self.routes[hit['id']]
            if not words(route['text'])&informative:continue
            for link in route['links']:
                fid=link['fact_id'];value=link['similarity']/(RULES['rrf_k']+index+1)
                if link['similarity']<0.65 and not words(self.facts[fid]['text'])&informative:continue
                candidates[fid][route['level']]=max(candidates[fid].get(route['level'],0),value)
                route_proof[fid].append(route['id'])
        scores={fid:sum(v.values())+(1/(RULES['rrf_k']+rank[fid]) if fid in rank else 0) for fid,v in candidates.items()}
        chosen=list(flat[:RULES['protected_flat_windows']]);covered={fid for w in chosen for fid in w['fact_ids']};added=0
        protected_dates={self.facts[fid]['observed_at'] for fid in covered}
        for fid in sorted(scores,key=lambda x:(-scores[x],x)):
            if fid in covered:continue
            if self.facts[fid]['observed_at'] in protected_dates:continue
            ids=[x for x in self.window(fid) if x not in covered]
            proofs=route_proof[fid]
            chosen.append({'center':fid,'fact_ids':ids,'channel':'upper_navigation','route_ids':proofs,
                'route_levels':sorted({self.routes[x]['level'] for x in proofs})})
            covered.update(ids);added+=1
            if added==RULES['upper_extra_windows'] or len(chosen)==RULES['max_windows']:break
        for w in flat[RULES['protected_flat_windows']:]:
            if len(chosen)>=RULES['max_windows']:break
            if w['center'] in covered:continue
            ids=[x for x in w['fact_ids'] if x not in covered]
            if ids:chosen.append({**w,'fact_ids':ids});covered.update(ids)
        return chosen,{'consulted_levels':['L1','L2','L3','L4','L5'],'route_hits':len(route_hits),'upper_windows':added,
            'guard':'compositional query; informative-token support; novel observation date'}
    def render(self,windows,query):
        rows=[];date_query=a0.plan_date_query(query) is not None;cal=[];wk=[];seen=set()
        for index,window in enumerate(windows):
            fs=[self.facts[fid] for fid in window['fact_ids']]
            if not fs:continue
            assert len({f['observed_at'] for f in fs})==1
            row={'id':window['center'],'created_at':fs[0]['observed_at'],
                'content':'Derived memory summary:\n'+'\n'.join(f"[{f['observed_at']}] memory: {f['text']}" for f in fs)}
            rows.append(row)
            if date_query:
                for f in fs:
                    for entry in f['annotations']:
                        d=entry['data'];key=(entry['kind'],d['source_id'],tuple(d['span']))
                        if key in seen:continue
                        seen.add(key);(cal if entry['kind']=='calendar' else wk).append((len(rows)-1,entry))
        added=0;rendered=[];skips=[]
        for kind,entries in [('calendar',cal),('week',wk)]:
            grouped=collections.defaultdict(list)
            for i,e in entries:grouped[i].append(e)
            required=sum(sum(len(e['line'])+1 for e in es)+(len(previous.HEADER)+2 if previous.HEADER not in rows[i]['content'] else 0) for i,es in grouped.items())
            if added+required>RULES['annotation_budget']:
                skips.append(kind);continue
            for i,es in grouped.items():
                rows[i]['content']+= ('\n' if previous.HEADER in rows[i]['content'] else '\n\n'+previous.HEADER+'\n')+'\n'.join(e['line'] for e in es)
                rendered.extend(es)
            added+=required
        evidence=format_memories(rows,question=query,options=None)
        return evidence,{'evidence_rows':len(rows),'fact_ids':[fid for w in windows for fid in w['fact_ids']],
            'annotations_rendered':len(rendered),'annotation_added_chars':added,'annotation_skips':skips,
            'observed_at_source':'canonical lower fact; no parent-summary date',
            'evidence_chars':len(evidence),'main_budget':'W0 117760 tokens minus query','new_annotation_budget':8000}

def prepare():
    retriever=Retriever();questions=read(W0/'frozen/questions.json')['questions'];audit=collections.Counter();identical=0
    for q in questions:
        pair={}
        for arm in ['lower_only','all_levels']:
            windows,trace=retriever.windows(q['sample_id'],q['question'],arm=='all_levels')
            evidence,details=retriever.render(windows,q['question'])
            facts=details['fact_ids'];assert len(facts)==len(set(facts))
            assert all(retriever.facts[fid]['conv']==q['sample_id'] for fid in facts)
            assert len(windows)<=RULES['max_windows'] and details['annotation_added_chars']<=8000
            pair[arm]={'question_id':q['question_id'],'evidence':evidence,'windows':windows,'navigation':trace,'diagnostics':details}
            path=ROOT/'requests'/arm;path.mkdir(parents=True,exist_ok=True)
            write(path/(q['question_id'].replace('#','_')+'.json'),pair[arm])
            if arm=='all_levels':
                audit['upper_extra_windows']+=trace['upper_windows'];audit['queries_with_upper_extras']+=trace['upper_windows']>0
                audit['annotation_budget_skip_queries']+=bool(details['annotation_skips'])
        core=min(RULES['protected_flat_windows'],len(pair['lower_only']['windows']))
        assert pair['lower_only']['windows'][:core]==pair['all_levels']['windows'][:core]
        identical+=pair['lower_only']['evidence']==pair['all_levels']['evidence']
    report={'created_at':now(),'passed':True,'queries':861,'identical_evidence_pairs':identical,'counts':dict(audit),
        'duplicate_fact_ids':0,'cross_conversation_facts':0,'protected_lower_windows_equal':True,
        'same_model_formatter_budget_and_memory_bank':True,'runtime_reads_L0':False,'queries_or_gold_used_to_construct_store':False}
    write(ROOT/'rules.json',RULES);write(ROOT/'input_audit.json',report);print('INPUTS',json.dumps(report),flush=True)

if __name__=='__main__':
    construct();prepare()
