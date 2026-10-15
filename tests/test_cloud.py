import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import cloud
from agent import Agent
from cloud import youtube_url

class Links(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(youtube_url('https://youtu.be/pHkGEKuwXUw?si=test'),'https://www.youtube.com/watch?v=pHkGEKuwXUw')
        self.assertIn('playlist?list=',youtube_url('https://www.youtube.com/watch?v=pHkGEKuwXUw&list=PLswYkv98UynRbeZXA3GWEKA_U0An19weF'))
    def test_reject(self):
        for value in ['http://youtube.com/watch?v=pHkGEKuwXUw','https://youtube.com.evil.test/watch?v=pHkGEKuwXUw','https://user@youtube.com/watch?v=pHkGEKuwXUw','https://youtu.be/../../file','https://localhost/']:
            with self.assertRaises(ValueError): youtube_url(value)

class Progress(unittest.TestCase):
    def test_cloud_does_not_seed_and_restores_completion(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'work'; state=Path(d)/'state'
            a=Agent(root,seed=False,log=lambda _:None)
            self.assertEqual(a.rows(),[])
            with a.db() as c:
                c.execute('INSERT INTO videos(id,title,url,position,state) VALUES(?,?,?,?,?)',
                  ('pHkGEKuwXUw','test','https://www.youtube.com/watch?v=pHkGEKuwXUw',1,'complete'))
            out=root/'results'/'pHkGEKuwXUw'; out.mkdir()
            (out/'transcript.txt').write_text('متن آزمایشی')
            with patch.object(cloud,'ROOT',root),patch.object(cloud,'STATE',state):
                cloud.checkpoint(a)
                b=Agent(Path(d)/'second',seed=False,log=lambda _:None)
                cloud.restore(b)
                self.assertEqual(b.rows()[0]['state'],'complete')
                self.assertNotIn('local_path',(state/'queue.json').read_text())
                self.assertTrue((root/'Ahsan-Qasas-Review-Export.zip').exists())

if __name__=='__main__': unittest.main()
