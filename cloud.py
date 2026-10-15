"""Bounded free CPU queue. No paid API, no invented summaries or source verification."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit, parse_qs
from agent import Agent, PLAYLIST, atomic_json

ROOT = Path('work')
STATE = Path('state')

def youtube_url(value):
    u = urlsplit(value.strip())
    if u.scheme != 'https' or u.hostname not in {'youtube.com','www.youtube.com','youtu.be'} or u.username or u.password or u.port:
        raise ValueError('Only public HTTPS YouTube video or playlist links are accepted.')
    q = parse_qs(u.query)
    if q.get('list') and re.fullmatch(r'[A-Za-z0-9_-]{10,100}',q['list'][0]):
        return 'https://www.youtube.com/playlist?list='+q['list'][0]
    vid = u.path.strip('/') if u.hostname == 'youtu.be' else q.get('v',[''])[0]
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}',vid):
        raise ValueError('Invalid video ID.')
    return 'https://www.youtube.com/watch?v='+vid

def restore(a):
    p = STATE/'queue.json'
    if p.exists():
        rows = json.loads(p.read_text())
        with a.db() as c:
            c.execute('DELETE FROM videos')
            for r in rows:
                c.execute('INSERT INTO videos(id,title,url,position,state,error,updated) VALUES(?,?,?,?,?,?,?)',
                    (r['id'],r['title'],r['url'],r['position'],r['state'],r.get('error',''),time.time()))
    results = STATE/'results'
    if results.exists():
        import shutil
        shutil.copytree(results, ROOT/'results',dirs_exist_ok=True)

def checkpoint(a):
    import shutil
    STATE.mkdir(exist_ok=True)
    atomic_json(STATE/'queue.json',[{k:r[k] for k in ('id','title','url','position','state','error')} for r in a.rows()])
    shutil.copytree(ROOT/'results',STATE/'results',dirs_exist_ok=True)
    rows=a.rows()
    done=sum(r['state']=='complete' for r in rows)
    errors=sum(r['state']=='error' for r in rows)
    lines=['# گزارش پردازش احسن القصص',f'پردازش کامل: {done} از {len(rows)}؛ خطا: {errors}',
      'این گزارش بررسی علمی یا تأیید صحت گفتار نیست. متن‌ها خروجی خودکار تشخیص گفتارند.',
      'هیچ کتاب یا خلاصهٔ تحلیلی خودکار تولید نشده است. موارد دینی باید جداگانه بررسی شوند.','']
    for r in rows:
        lines.append(f"- {r['title']} | {r['state']} | {r['url']}")
    (STATE/'STATUS.md').write_text('\n'.join(lines),encoding='utf-8')
    a.export()

def telegram_export(path):
    token=os.environ.get('TELEGRAM_BOT_TOKEN')
    chat=os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat:
        return
    import requests
    try:
        if path.stat().st_size > 45_000_000:
            print('Telegram export too large; use GitHub artifact.')
            return
        with path.open('rb') as f:
            r=requests.post('https://api.telegram.org/bot'+token+'/sendDocument',
                data={'chat_id':chat,'caption':'خروجی گفتار برای بررسی؛ کتاب نهایی نیست.'},
                files={'document':(path.name,f,'application/zip')},timeout=90)
        if not r.ok or not r.json().get('ok'):
            print('Telegram delivery failed; GitHub results remain available.')
    except Exception:
        print('Telegram unavailable; no credentials logged.')

def main():
    if len(sys.argv)>1 and sys.argv[1]=='episode':
        a=Agent(ROOT,seed=False)
        a.process([sys.argv[2]],model=os.environ.get('ASR_MODEL','medium'),sample=False,allow_download=True)
        return
    a=Agent(ROOT,seed=False)
    restore(a)
    STATE.mkdir(exist_ok=True)
    source=STATE/'source.json'
    submitted=os.environ.get('SOURCE_URL','').strip()
    url=youtube_url(submitted or (json.loads(source.read_text())['url'] if source.exists() else PLAYLIST))
    if source.exists() and json.loads(source.read_text())['url']!=url:
        raise ValueError('Use a separate repository for a different source; existing results were preserved.')
    atomic_json(source,{'url':url})
    try:
        if 'playlist?' in url:
            a.discover(url)
            atomic_json(STATE/'discovered.json',[r['id'] for r in a.rows()])
        else:
            vid=parse_qs(urlsplit(url).query)['v'][0]
            with a.db() as c:
                c.execute('DELETE FROM videos WHERE id != ?',(vid,))
                c.execute('INSERT OR IGNORE INTO videos(id,title,url,position,updated) VALUES(?,?,?,?,?)',(vid,vid,url,1,time.time()))
    except Exception:
        checkpoint(a)
        print('Public playlist access failed. No spoken review performed; saved prior results.')
        raise
    attempts_path=STATE/'attempts.json'
    attempts=json.loads(attempts_path.read_text()) if attempts_path.exists() else {}
    deadline=time.monotonic()+18*60
    processed=0
    for row in a.rows():
        if row['state']=='complete' or attempts.get(row['id'],0)>=2:
            continue
        remaining=int(deadline-time.monotonic())
        if remaining<90 or processed>=2: break
        attempts[row['id']]=attempts.get(row['id'],0)+1
        atomic_json(attempts_path,attempts)
        try:
            subprocess.run([sys.executable,__file__,'episode',row['id']],timeout=min(900,remaining),check=True)
        except subprocess.TimeoutExpired:
            a.update(row['id'],state='error',error='Episode exceeded CPU time budget; not complete.')
        except subprocess.CalledProcessError:
            a.update(row['id'],state='error',error='Worker failed; not complete.')
        processed+=1
        checkpoint(a)
        now=next(r for r in a.rows() if r['id']==row['id'])
        if now['state']=='error': break
    checkpoint(a)
    if processed: telegram_export(ROOT/'Ahsan-Qasas-Review-Export.zip')
    if any(r['state']=='error' for r in a.rows()):
        print('Queue contains failed episodes. Saved report is available; this is NOT successful transcription.')
        raise SystemExit(1)

if __name__=='__main__':
    main()
