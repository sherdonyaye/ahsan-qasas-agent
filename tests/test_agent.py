"""Queue/output tests use a controlled fake decoder, not a real speech model."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import zipfile
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent import Agent

class Decoder:
    def __init__(self, empty=False, fail=False):
        self.calls = 0
        self.empty = empty
        self.fail = fail
    def transcribe(self,path,**kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError('Decoder failed')
        items=[] if self.empty else [SimpleNamespace(start=1.125,end=3.5,text=' متن آزمایشی قرآن ',avg_logprob=-1.5,no_speech_prob=0)]
        return iter(items),SimpleNamespace(duration=10)

class Tests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.root=Path(self.t.name)
        self.a=Agent(self.root/'work',log=lambda s:None)
        self.media=self.root/'test.mp3'
        self.media.write_bytes(b'controlled-test-media')
        self.vid='pHkGEKuwXUw'
        self.a.add_local([self.media],self.vid)
    def row(self):
        return next(r for r in self.a.rows() if r['id']==self.vid)
    def test_sample_not_full_and_confidence_flag(self):
        self.a.process([self.vid],model_override=Decoder(),sample=True)
        self.assertEqual(self.row()['state'],'sample')
        out=self.a.root/'results'/self.vid
        self.assertFalse((out/'transcript.json').exists())
        d=json.loads((out/'sample.json').read_text())
        self.assertTrue(d['sample_only'])
        self.assertTrue(d['segments'][0]['flags'])
        self.assertIn('00:00:01,125 --> 00:00:03,500',(out/'sample.srt').read_text(encoding='utf-8-sig'))
    def test_completed_survives_restart_and_is_skipped(self):
        engine=Decoder()
        self.a.process([self.vid],model_override=engine,sample=False)
        restarted=Agent(self.a.root,log=lambda s:None)
        restarted.process([self.vid],model_override=engine,sample=False)
        self.assertEqual(engine.calls,1)
        self.assertEqual(self.row()['state'],'complete')
    def test_decoder_error_and_empty_not_marked_complete(self):
        for engine in [Decoder(fail=True),Decoder(empty=True)]:
            self.a.process([self.vid],model_override=engine,sample=False)
            self.assertEqual(self.row()['state'],'error')
    def test_local_duplicate_and_filename_cannot_escape(self):
        f=self.root/'strange .. [name].wav'
        f.write_bytes(b'new-content')
        one=self.a.add_local([f])
        two=self.a.add_local([f])
        self.assertEqual(one,two)
        self.assertTrue(one[0].startswith('local-'))
        self.assertEqual(len(self.a.rows()),7)
    def test_export_has_text_not_media_or_absolute_paths(self):
        self.a.process([self.vid],model_override=Decoder(),sample=False)
        with zipfile.ZipFile(self.a.export()) as archive:
            names=archive.namelist()
            self.assertIn('results/'+self.vid+'/transcript.json',names)
            self.assertFalse(any('media/' in n or 'models/' in n for n in names))
            self.assertNotIn(str(self.root),archive.read('review-index.json').decode())
    def test_cancel_preserves_noncomplete_state(self):
        stop=self.a.stop
        class StoppingDecoder(Decoder):
            def transcribe(self,*args,**kwargs):
                segments,info=super().transcribe(*args,**kwargs)
                def gen():
                    for s in segments:
                        stop.set()
                        yield s
                return gen(),info
        self.a.process([self.vid],model_override=StoppingDecoder(),sample=False)
        self.assertEqual(self.row()['state'],'interrupted')
    def test_replacement_archives_previous_transcript(self):
        self.a.process([self.vid],model_override=Decoder(),sample=False)
        self.a.add_local([self.media],self.vid)
        self.assertEqual(self.row()['state'],'pending')
        self.assertFalse((self.a.root/'results'/self.vid).exists())
        self.assertTrue(list((self.a.root/'archived').iterdir()))
        with zipfile.ZipFile(self.a.export()) as z:
            self.assertFalse(any(n.endswith('transcript.json') for n in z.namelist()))
    def test_interrupted_previous_session_not_complete(self):
        self.a.update(self.vid,state='running')
        Agent(self.a.root,log=lambda s:None)
        self.assertEqual(self.row()['state'],'interrupted')

if __name__=='__main__':
    unittest.main()
