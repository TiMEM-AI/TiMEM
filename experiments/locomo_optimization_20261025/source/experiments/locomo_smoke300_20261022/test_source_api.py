import importlib.util,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,'F:/worktable/research/timem-aml')
from fastapi.testclient import TestClient
from server.settings import ServerSettings
from server.idempotency import IdempotencyStore
from server.model_settings import ModelSettingsStore
spec=importlib.util.spec_from_file_location('staged_app',ROOT/'app.staged.py')
appmod=importlib.util.module_from_spec(spec);spec.loader.exec_module(appmod)
class Fake:
    def __init__(self): self.adds=0
    async def add(self,*a,**kw): self.adds+=1
    async def search(self,*a,**kw): return []
class APITests(unittest.TestCase):
    def test_add_search_replay_and_isolation(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            root=Path(temp); fake=Fake()
            with patch.dict(os.environ,{'TIMEM_SOURCE_EVIDENCE_PATH':str(root/'sources.sqlite')}),patch('server.time_anchor.stamp_temporal',return_value=0),patch('server.l2_backfill.schedule'):
                client=TestClient(appmod.create_app(fake,ServerSettings('secret','local','127.0.0.1',8000),IdempotencyStore(str(root/'adds.sqlite')),ModelSettingsStore(str(root/'models.json'))))
                headers={'X-API-Key':'secret'}
                payload={'request_id':'r1','user_id':'owner','session_id':'s','messages':[{'role':'user','content':'I ran a charity race last Saturday.','timestamp':'2023-05-25T00:00:00Z'}]}
                self.assertEqual(client.post('/aml/add',headers=headers,json=payload).status_code,200)
                self.assertEqual(client.post('/aml/add',headers=headers,json=payload).status_code,200)
                changed={**payload,'messages':[{'content':'POISON'}]}
                self.assertEqual(client.post('/aml/add',headers=headers,json=changed).status_code,200)
                self.assertEqual(fake.adds,1)
                query={'user_id':'owner','query':'When was the charity race?','top_k':1}
                result=client.post('/aml/search',headers=headers,json=query)
                self.assertEqual(result.status_code,200)
                row=result.json()['data'][0]
                self.assertEqual(set(row),{'id','content','score','created_at'})
                self.assertIn('2023-05-25',row['content']);self.assertNotIn('POISON',row['content'])
                self.assertEqual(client.post('/aml/search',headers=headers,json={**query,'user_id':'other'}).json()['data'],[])
                self.assertEqual(client.post('/aml/search',json=query).status_code,401)
                with patch.dict(os.environ,{'TIMEM_SOURCE_EVIDENCE':'0'}):
                    self.assertEqual(client.post('/aml/search',headers=headers,json=query).json()['data'],[])
if __name__=='__main__': unittest.main(verbosity=2)
