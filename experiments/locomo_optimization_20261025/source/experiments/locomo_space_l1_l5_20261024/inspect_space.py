"""Read-only TiMEM Space inspection using an existing local credential."""
import ast,json,collections
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parent
tree=ast.parse(Path('F:/worktable/doc/export_hierarchy.py').read_text(encoding='utf-8-sig'))
settings={}
for node in tree.body:
    if isinstance(node,ast.Assign):
        for target in node.targets:
            if isinstance(target,ast.Name) and target.id in {'SPACE','KEY'}:
                settings[target.id]=ast.literal_eval(node.value)
ROOT.mkdir(exist_ok=True)
with httpx.Client(timeout=40,trust_env=False) as client:
    for label,path,params in [('list','/memories',{'page':1,'page_size':5}),('tree','/memories/tree',None)]:
        response=client.get(settings['SPACE']+path,headers={'X-API-Key':settings['KEY']},params=params)
        try:data=response.json()
        except Exception:data={'non_json_length':len(response.content)}
        (ROOT/f'probe_{label}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'label':label,'status':response.status_code,'keys':list(data) if isinstance(data,dict) else type(data).__name__,
                         'shape':{k:(list(v) if isinstance(v,dict) else len(v) if isinstance(v,list) else v) for k,v in data.items() if k in {'success','message','total','data','detail'}} if isinstance(data,dict) else {}},ensure_ascii=False),flush=True)
