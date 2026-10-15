"""Actions-only worker: no YouTube request and no Telegram polling."""
import os, re, time
from pathlib import Path
import requests
from agent import Agent

def main():
    token=os.environ['TELEGRAM_BOT_TOKEN']
    vid=os.environ['VIDEO_ID']
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}',vid): raise ValueError('Invalid video ID')
    r=requests.post('https://api.telegram.org/bot'+token+'/getFile',data={'file_id':os.environ['FILE_ID']},timeout=60)
    if not r.ok or not r.json().get('ok'): raise RuntimeError('Telegram could not provide audio; check secret and file size')
    file_path=r.json()['result']['file_path']
    a=Agent(Path('work/cloud-telegram'),seed=False)
    media=a.root/'media'/(vid+'.webm')
    with requests.get('https://api.telegram.org/file/bot'+token+'/'+file_path,stream=True,timeout=180) as response:
        if not response.ok: raise RuntimeError('Audio transfer failed')
        with media.open('wb') as f:
            for chunk in response.iter_content(1024*1024): f.write(chunk)
    with a.db() as db:
        db.execute('INSERT INTO videos(id,title,url,position,updated) VALUES(?,?,?,?,?)',(vid,vid,'https://www.youtube.com/watch?v='+vid,1,time.time()))
    a.update(vid,local_path=str(media.resolve()))
    a.process([vid],model='medium',sample=False,allow_download=False)
    if a.rows()[0]['state']!='complete': raise RuntimeError('Transcription failed; not a successful result')

if __name__=='__main__':
    # Do not expose exception URLs, which can contain credentials.
    try: main()
    except Exception:
        print('Cloud transcription failed. Verify Telegram secret, file size, and model availability.')
        raise SystemExit(1)
