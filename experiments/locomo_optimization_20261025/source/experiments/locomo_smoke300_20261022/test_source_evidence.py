import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('source_evidence',Path(__file__).with_name('source_evidence.py'))
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.store=module.SourceEvidenceStore(Path(self.tmp.name)/'test.sqlite')
        self.messages=[
            {'role':'user','content':'Hello Ben, I joined the library yesterday.','timestamp':'2023-05-08T12:00:00Z'},
            {'role':'assistant','content':'Hi Alice, I ran the charity race last Saturday.','timestamp':'2023-05-25T12:00:00Z'},
            {'role':'user','content':'Who performed at the concert?','timestamp':'2023-06-01T12:00:00Z'},
            {'role':'assistant','content':'Summer Sounds and Matt Patterson performed.','timestamp':'2023-06-01T12:00:00Z'}]
        self.store.put('owner','session','request',self.messages)
    def tearDown(self): self.tmp.cleanup()
    def test_per_utterance_date_and_no_cross_date_context(self):
        rows=self.store.search('owner','charity race',10)
        self.assertEqual(len(rows),1)
        self.assertIn('2023-05-25',rows[0]['content'])
        self.assertNotIn('2023-05-08',rows[0]['content'])
        self.assertIn('last Saturday',rows[0]['content'])
    def test_neighbor_preserves_answer_without_keyword(self):
        rows=self.store.search('owner','concert',10)
        self.assertIn('Matt Patterson',rows[0]['content'])
    def test_ownership_is_enforced_for_candidates_and_neighbors(self):
        self.store.put('foreign','session','other',[{'content':'charity race secret','timestamp':'2023-05-25T12:00:00Z'}])
        rows=self.store.search('owner','charity race',20)
        self.assertNotIn('secret',str(rows))
        self.assertEqual(self.store.search('unknown','charity race',20),[])
    def test_replay_is_idempotent_and_mismatched_payload_fails(self):
        self.assertEqual(self.store.put('owner','session','request',self.messages),0)
        with self.assertRaises(ValueError): self.store.put('owner','session','request',[{'content':'changed'}])
        self.assertEqual(len(self.store.search('owner','charity race',20)),1)
    def test_temporal_grounding_replaces_wrong_summary_dates(self):
        summary=[{'id':'derived','content':'The race was on 2023-05-06.'}]
        rows=module.ground_evidence('owner','When was the charity race?',summary,100,store=self.store)
        self.assertNotIn('2023-05-06',str(rows))
        self.assertTrue(rows[0]['id'].startswith('src:'))
    def test_multihop_keeps_context_and_respects_limit(self):
        summary=[{'id':'derived','content':'Alice and Ben enjoy music.'}]
        rows=module.ground_evidence('owner','Who performed at the concert?',summary,10,store=self.store)
        self.assertIn('Matt Patterson',str(rows)); self.assertIn('derived',str(rows))
        self.assertEqual(len(module.ground_evidence('owner','concert',summary,1,store=self.store)),1)
    def test_missing_timestamp_is_not_invented(self):
        self.store.put('undated','s','r',[{'content':'A violin concert.'}])
        self.assertIn('time unknown',self.store.search('undated','concert')[0]['content'])
    def test_no_match_falls_back(self):
        summary=[{'id':'derived','content':'semantic-only fact'}]
        self.assertEqual(module.ground_evidence('owner','zyxwvu',summary,5,store=self.store),summary)

if __name__=='__main__': unittest.main(verbosity=2)
