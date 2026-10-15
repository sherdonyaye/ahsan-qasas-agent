import importlib.util, sys, tempfile, threading, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from agent import Agent

root=Path(__file__).resolve().parents[1]
with patch.dict(sys.modules,{'requests':SimpleNamespace()}):
    spec=importlib.util.spec_from_file_location('bridge_under_test',root/'github_bridge.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

class Bridge(unittest.TestCase):
    def make(self,d):
        b=object.__new__(mod.CloudBot)
        b.root=Path(d);b.agent=Agent(Path(d)/'queue',seed=False)
        b.stop=SimpleNamespace(wait=lambda seconds:False)
        b.say=lambda *args:None;b.log=lambda *args:None
        b.config={'owner':1}
        vid='pHkGEKuwXUw'
        media=b.agent.root/'media'/(vid+'.webm');media.write_bytes(b'fakeaudio')
        with b.agent.db() as db:
            db.execute('INSERT INTO videos(id,title,url,local_path) VALUES(?,?,?,?)',(vid,'Episode','https://youtube.com/watch?v='+vid,str(media)))
        b.api=lambda *args,**kwargs:{'document':{'file_id':'fake-file-id'}}
        return b,vid

    def test_failed_cloud_job_is_not_complete(self):
        with tempfile.TemporaryDirectory() as d:
            b,vid=self.make(d)
            def github(method,path,**kwargs):
                if method=='POST':
                    self.ticket=kwargs['json']['inputs']['ticket'];return None
                return {'workflow_runs':[{'display_title':self.ticket,'status':'completed','conclusion':'failure','html_url':'https://github.com/example/run'}]}
            b.github=github
            b.cloud_process([vid])
            self.assertEqual(b.agent.rows()[0]['state'],'error')
            self.assertFalse((b.root/(vid+'-cloud.json')).exists())

    def test_stopped_wait_retains_checkpoint_for_reconnect(self):
        with tempfile.TemporaryDirectory() as d:
            b,vid=self.make(d)
            b.stop=SimpleNamespace(wait=lambda seconds:True)
            b.github=lambda *args,**kwargs:None
            b.cloud_process([vid])
            self.assertEqual(b.agent.rows()[0]['state'],'interrupted')
            self.assertTrue((b.root/(vid+'-cloud.json')).exists())

if __name__=='__main__':unittest.main()

class ArtifactPaths(unittest.TestCase):
    def test_flat_and_nested_outputs_both_load(self):
        import io,json,zipfile
        vid='ndyUFPMNjMA'
        for prefix in ['',vid+'/']:
            data=io.BytesIO()
            with zipfile.ZipFile(data,'w') as z:
                for name in ['transcript.txt','transcript.srt','transcript-source-cues.txt']:
                    z.writestr(prefix+name,'sample')
                z.writestr(prefix+'transcript.json',json.dumps({'id':vid,'sample_only':False,'processing_finished':True}))
            with tempfile.TemporaryDirectory() as d:
                mod.save_cloud_output(data.getvalue(),Path(d),vid)
                self.assertEqual((Path(d)/'transcript.txt').read_text(),'sample')

    def test_wrong_episode_cannot_be_imported(self):
        import io,json,zipfile
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:
            for name in ['transcript.txt','transcript.srt','transcript-source-cues.txt']:z.writestr(name,'sample')
            z.writestr('transcript.json',json.dumps({'id':'wrong','sample_only':False,'processing_finished':True}))
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError):mod.save_cloud_output(data.getvalue(),Path(d),'ndyUFPMNjMA')
            self.assertFalse((Path(d)/'transcript.txt').exists())
