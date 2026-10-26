async def generate(manifest,args):
 nodes=manifest['nodes'];path=ROOT/'generated_memories.json'
 state=read(path) if path.exists() else {'run':RUN,'source_sha256':source_hash(),'records':{},'errors':{}}
 assert state['source_sha256']==manifest['source_sha256']
 content=state['records'];endpoints=_score_endpoints();sem=asyncio.Semaphore(args.concurrency);lock=asyncio.Lock()
 async with httpx.AsyncClient(timeout=240,trust_env=False) as client:
  async def one(nid):
   if nid in content:return
   n=nodes[nid];children=n['child_ids']
   if children and len(children)==1:
    text=content[children[0]]['content'];method='single-child preservation';metadata={}
   else:
    prompt=prompt_for(n,nodes,content);body=None
    async with sem:
     for attempt in range(4):
      try:
       response=await client.post(endpoints['answer_base'].rstrip('/')+'/chat/completions',
        headers={'Authorization':'Bearer '+endpoints['answer_key']},
        json={'model':MODEL,'temperature':0,'messages':[{'role':'user','content':prompt}]})
       response.raise_for_status();body=response.json();text=str(body['choices'][0]['message']['content']).strip()
       if len(text)<25:raise ValueError('empty or trivial memory')
       break
      except Exception:
       if attempt==3:raise
       await asyncio.sleep(2**attempt)
    method='DeepSeek consolidation';metadata={'returned_model':body.get('model'),'response_id':body.get('id'),'usage':body.get('usage'),
     'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest()}
   value={k:v for k,v in n.items() if k!='input'}
   rendered=text if method=='single-child preservation' else f"Observation window: {n['start']} to {n['end']}\n"+text
   value.update({'content':rendered,
     'generation_method':method,'generated_at':now(),**metadata})
   async with lock:
    content[nid]=value;state['errors'].pop(nid,None);write(path,state)
    if len(content)%20==0 or args.mode=='pilot':print(f'generated {len(content)}/{len(nodes)} {n["level"]}',flush=True)
  if args.mode=='pilot':
   nid=next(nid for nid,n in nodes.items() if n['level']=='L1');await one(nid)
  else:
   for level in ['L1','L2','L3','L4','L5']:
    ids=[nid for nid,n in nodes.items() if n['level']==level]
    results=await asyncio.gather(*(one(nid) for nid in ids),return_exceptions=True)
    errors=[str(x) for x in results if isinstance(x,Exception)]
    if errors:raise RuntimeError(f'{level} generation failed: {len(errors)}')
    print('LEVEL COMPLETE',level,len(ids),flush=True)
 return state
