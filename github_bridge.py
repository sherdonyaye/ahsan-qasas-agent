"""Serial Telegram audio -> Actions bridge. Credentials are never persisted."""
import getpass, io, json, os, re, time, uuid, zipfile
from pathlib import Path
import requests
from telegram_bot import Bot, acquire_lock

REPO='sherdonyaye/ahsan-qasas-agent'
WORKFLOW='telegram-transcribe.yml'

def compact_audio(source,stop):
    """Use installed PyAV to create mono low bitrate speech audio, no paid API."""
    import av
    target=source.with_suffix('.cloud.ogg')
    if target.exists() and target.stat().st_size>0: return target
    temporary=target.with_suffix('.partial.ogg')
    try:
        with av.open(str(source)) as src, av.open(str(temporary),'w',format='ogg') as dst:
            stream=dst.add_stream('libopus',rate=48000)
            stream.bit_rate=24000
            stream.layout='mono'
            resampler=av.AudioResampler(format='fltp',layout='mono',rate=48000)
            for frame in src.decode(audio=0):
                if stop.is_set(): raise RuntimeError('Stopped during audio compression')
                for converted in resampler.resample(frame):
                    converted.pts=None
                    for packet in stream.encode(converted): dst.mux(packet)
            for converted in resampler.resample(None):
                converted.pts=None
                for packet in stream.encode(converted): dst.mux(packet)
            for packet in stream.encode(None): dst.mux(packet)
        temporary.replace(target)
        return target
    finally:
        temporary.unlink(missing_ok=True)

class CloudBot(Bot):
    def __init__(self,token,github_token):
        super().__init__(token)
        self.gh=requests.Session()
        self.gh.headers.update({'Authorization':'Bearer '+github_token,'Accept':'application/vnd.github+json'})
        self.agent.process=self.cloud_process

    def document(self,path):
        if path.suffix.lower() in {'.webm','.m4a','.mp3','.ogg','.opus'}:
            return  # cloud_process uploads audio once and retains Telegram file_id
        return super().document(path)

    def say(self,text,chat=None):
        text=text.replace('مدل medium؛ دفعهٔ اول دانلود مدل هم لازم است.','مدل medium در GitHub اجرا می‌شود.')
        return super().say(text,chat)

    def github(self,method,path,**kwargs):
        r=self.gh.request(method,'https://api.github.com/repos/'+REPO+path,timeout=60,**kwargs)
        if not r.ok: raise RuntimeError('GitHub request failed: HTTP '+str(r.status_code))
        return r.json() if r.content else None

    def cloud_process(self,ids,**kwargs):
        for vid in ids:
            row=next(r for r in self.agent.rows() if r['id']==vid)
            media=self.agent.audio(row,False)
            if media.stat().st_size>19_000_000:
                media=compact_audio(media,self.stop)
            # Telegram's cloud getFile cannot fetch large documents. No silent truncation.
            if media.stat().st_size>19_000_000:
                self.agent.update(vid,state='error',error='Audio exceeds safe Telegram getFile size; split or compress before cloud processing.')
                self.say('صوت این قسمت برای انتقال ابری بزرگ است؛ باید فشرده یا بخش‌بندی شود. صف متوقف شد.')
                return
            checkpoint=self.root/(vid+'-cloud.json')
            state=json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
            if not state:
                with media.open('rb') as f:
                    msg=self.api('sendDocument',{'chat_id':self.config['owner']},{'document':(media.name,f)},timeout=180)
                ticket='qasas-'+vid+'-'+uuid.uuid4().hex[:12]
                state={'ticket':ticket,'file_id':msg['document']['file_id']}
                from agent import atomic_json
                atomic_json(checkpoint,state)
            if not state.get('dispatched'):
                self.github('POST','/actions/workflows/'+WORKFLOW+'/dispatches',json={'ref':'main','inputs':{'ticket':ticket,'video_id':vid,'file_id':state['file_id']}})
                state['dispatched']=True
                from agent import atomic_json
                atomic_json(checkpoint,state)
            self.agent.update(vid,state='running',error='')
            self.say('تبدیل در GitHub شروع شد: '+row['title']+'؛ پس از پایان، قسمت بعد خودکار ادامه می‌یابد.')
            deadline=time.monotonic()+6*3600
            run=None
            while time.monotonic()<deadline:
                if self.stop.wait(20):
                    self.agent.update(vid,state='interrupted',error='Cloud job may still run; /continue reconnects.')
                    return
                runs=self.github('GET','/actions/workflows/'+WORKFLOW+'/runs?event=workflow_dispatch&per_page=100')['workflow_runs']
                run=next((r for r in runs if r['display_title']==state['ticket']),None)
                self.log('GitHub: '+(run['status'] if run else 'waiting for run'))
                if run and run['status']=='completed': break
            if not run or run['status']!='completed': raise RuntimeError('Cloud wait timed out; checkpoint retained.')
            if run['conclusion']!='success':
                checkpoint.unlink(missing_ok=True)
                self.agent.update(vid,state='error',error='GitHub conversion failed: '+run['html_url'])
                self.say('تبدیل ابری موفق نشد؛ جزئیات: '+run['html_url'])
                return
            assets=self.github('GET',f"/actions/runs/{run['id']}/artifacts")['artifacts']
            artifact=next(a for a in assets if a['name']==state['ticket'])
            response=self.gh.get(artifact['archive_download_url'],timeout=180)
            if not response.ok: raise RuntimeError('Could not retrieve cloud output')
            out=self.agent.root/'results'/vid
            out.mkdir(parents=True,exist_ok=True)
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                for name in ['transcript.txt','transcript.srt','transcript.json','transcript-source-cues.txt']:
                    (out/name).write_bytes(z.read(name))
            report=json.loads((out/'transcript.json').read_text())
            if report['id']!=vid or report['sample_only'] or not report['processing_finished']:
                raise RuntimeError('Invalid cloud result')
            self.agent.update(vid,state='complete',error='')
            checkpoint.unlink(missing_ok=True)

if __name__=='__main__':
    lock=acquire_lock()
    token=os.environ.get('TELEGRAM_BOT_TOKEN') or getpass.getpass('Telegram token (hidden): ').strip()
    gh=os.environ.get('QASAS_GITHUB_TOKEN') or getpass.getpass('GitHub token, Actions read/write (hidden): ').strip()
    if not token or not gh: raise SystemExit('Both credentials are required.')
    CloudBot(token,gh).run()
