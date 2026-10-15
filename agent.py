"""Local-only Persian speech collector. No API key or paid service."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from contextlib import contextmanager
import threading
import time
import zipfile

PLAYLIST = 'https://www.youtube.com/playlist?list=PLswYkv98UynRbeZXA3GWEKA_U0An19weF'
MEDIA = {'.mp3', '.mp4', '.m4a', '.wav', '.webm', '.ogg', '.opus', '.flac', '.mkv', '.mov'}
SEEDS = [
 ('pHkGEKuwXUw', '۱ — اصحاب اخدود'),
 ('ndyUFPMNjMA', '۲ — اصحاب رس'),
 ('hgy3_VXz6Io', '۳ — حدیث جساسه'),
 ('5bjcbACK94w', '۴ — دجال کیست؟'),
 ('Nm8SQK6e7Aw', '۵ — فتنه‌های دجال'),
 ('oj_LZuWGTm0', '۶ — یأجوج و مأجوج'),
]

def atomic_json(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)

def timestamp(seconds, srt=False):
    n = round(max(0, seconds) * 1000)
    h, n = divmod(n, 3600000)
    m, n = divmod(n, 60000)
    s, ms = divmod(n, 1000)
    return f'{h:02}:{m:02}:{s:02}' + (f',{ms:03}' if srt else '')

class Cancelled(Exception):
    pass

class Agent:
    def __init__(self, root, log=print, stop=None, seed=True):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ['media', 'results', 'models']:
            (self.root / name).mkdir(exist_ok=True)
        self.log = log
        self.stop = stop or threading.Event()
        self.model = None
        self.model_name = None
        with self.db() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS videos (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT DEFAULT '',
                local_path TEXT DEFAULT '', position INTEGER DEFAULT 99999,
                state TEXT DEFAULT 'pending', error TEXT DEFAULT '', updated REAL)''')
            c.execute("UPDATE videos SET state='interrupted', error='Previous run ended before completion.' WHERE state='running'")
            for i, (vid, title) in enumerate(SEEDS if seed else [], 1):
                c.execute('INSERT OR IGNORE INTO videos(id,title,url,position,updated) VALUES(?,?,?,?,?)',
                          (vid, title, 'https://www.youtube.com/watch?v=' + vid, i, time.time()))

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.root / 'queue.sqlite3', timeout=30)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def rows(self):
        with self.db() as c:
            return [dict(r) for r in c.execute('SELECT * FROM videos ORDER BY position,id')]

    def update(self, vid, **fields):
        allowed = {'state','error','local_path','title','position'}
        if not set(fields) <= allowed:
            raise ValueError('Invalid queue field')
        fields['updated'] = time.time()
        with self.db() as c:
            c.execute('UPDATE videos SET ' + ','.join(k + '=?' for k in fields) + ' WHERE id=?',
                      (*fields.values(), vid))

    def discover(self, url=PLAYLIST):
        from yt_dlp import YoutubeDL
        self.log('Reading playlist metadata. This does NOT read spoken content.')
        opts = {'extract_flat':True, 'skip_download':True, 'quiet':True,
                'socket_timeout':20, 'retries':0, 'ignoreerrors':True}
        with YoutubeDL(opts) as y:
            info = y.extract_info(url, download=False)
            if not info or 'entries' not in info:
                raise RuntimeError('Playlist was not accessible. Existing queue was kept.')
            count = 0
            for i, item in enumerate(info['entries'], 1):
                if self.stop.is_set():
                    raise Cancelled()
                if not item:
                    continue
                vid = item.get('id','')
                if not re.fullmatch(r'[A-Za-z0-9_-]{11}', vid):
                    continue
                with self.db() as c:
                    c.execute('''INSERT INTO videos(id,title,url,position,updated) VALUES(?,?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET title=excluded.title,
                        position=excluded.position,updated=excluded.updated''',
                        (vid, item.get('title') or vid, 'https://www.youtube.com/watch?v='+vid, i, time.time()))
                count += 1
        self.log(f'Accessible playlist entries: {count}. Unavailable entries were not invented.')
        return count

    def add_local(self, files, target_id=None):
        added = []
        if target_id and len(files) != 1:
            raise ValueError('Choose one file to attach to the selected episode.')
        for f in files:
            p = Path(f).resolve()
            if not p.is_file() or p.suffix.lower() not in MEDIA:
                raise ValueError(f'Unsupported media: {p.name}')
            if target_id:
                if target_id not in {r['id'] for r in self.rows()}:
                    raise ValueError('Episode is not in the queue')
                old = self.root/'results'/target_id
                if old.exists():
                    archive = self.root/'archived'
                    archive.mkdir(exist_ok=True)
                    old.rename(archive/(target_id+'-'+str(time.time_ns())))
                # A replacement recording must never inherit the old completion status.
                self.update(target_id, local_path=str(p), state='pending', error='')
                added.append(target_id)
            else:
                h = hashlib.sha256()
                with p.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024*1024), b''):
                        h.update(chunk)
                vid = 'local-' + h.hexdigest()[:24]
                with self.db() as c:
                    c.execute('INSERT OR IGNORE INTO videos(id,title,local_path,updated) VALUES(?,?,?,?)',
                              (vid, p.stem, str(p), time.time()))
                    c.execute('UPDATE videos SET local_path=? WHERE id=?', (str(p), vid))
                added.append(vid)
        return added

    def audio(self, row, allow_download):
        if row['local_path'] and Path(row['local_path']).is_file():
            return Path(row['local_path'])
        if not allow_download or not row['url']:
            raise RuntimeError('No local media. Attach a file, or explicitly enable public download.')
        from yt_dlp import YoutubeDL
        self.log('Trying public audio access; no cookies or sign-in bypass is used.')
        opts = {'format':'bestaudio/best', 'noplaylist':True, 'quiet':True,
                'socket_timeout':20, 'retries':0,
                'outtmpl':str(self.root/'media'/(row['id']+'.%(ext)s'))}
        with YoutubeDL(opts) as y:
            info = y.extract_info(row['url'], download=True)
            p = Path(y.prepare_filename(info)).resolve()
        if not p.is_file():
            raise RuntimeError('Audio download did not produce a file.')
        self.update(row['id'], local_path=str(p))
        return p

    def load_model(self, name):
        if name not in {'small','medium','large-v3'}:
            raise ValueError('Unsupported model')
        if self.model is None or self.model_name != name:
            from faster_whisper import WhisperModel
            self.log('Loading '+name+'. First use downloads the model; this can take time.')
            self.model = WhisperModel(name, device='cpu', compute_type='int8',
                cpu_threads=max(1, min(8, (os.cpu_count() or 2)-1)),
                download_root=str(self.root/'models'))
            self.model_name = name
        return self.model

    def process(self, ids, model='small', sample=True, allow_download=False, model_override=None):
        selected = set(ids)
        for row in self.rows():
            if row['id'] not in selected or self.stop.is_set():
                continue
            if row['state'] == 'complete':
                self.log('Already complete, skipped: '+row['title'])
                continue
            out = self.root/'results'/row['id']
            out.mkdir(exist_ok=True)
            self.update(row['id'], state='running', error='')
            try:
                media = self.audio(row, allow_download)
                if self.stop.is_set():
                    raise Cancelled()
                engine = model_override or self.load_model(model)
                if self.stop.is_set():
                    raise Cancelled()
                options = dict(language='fa', task='transcribe', beam_size=5,
                               vad_filter=True, condition_on_previous_text=False)
                if sample:
                    options['clip_timestamps'] = '0,120'
                segments, info = engine.transcribe(str(media), **options)
                data = []
                for seg in segments:
                    if self.stop.is_set():
                        raise Cancelled()
                    text = seg.text.strip()
                    if not text:
                        continue
                    flags = []
                    if getattr(seg, 'avg_logprob',0) < -1 or getattr(seg,'no_speech_prob',0) > .6:
                        flags.append('نیازمند بازشنوی')
                    data.append({'start':round(seg.start,3), 'end':round(seg.end,3),
                                 'text':text, 'flags':flags})
                    if len(data) % 10 == 0:
                        self.log(timestamp(seg.end)+' — '+row['title'])
                        atomic_json(out/'partial.json', data)
                if not data:
                    raise RuntimeError('No spoken text detected. Not marked complete.')
                report = {'id':row['id'], 'title':row['title'], 'source_url':row['url'],
                    'local_media_name':media.name, 'engine':'faster-whisper', 'model':model,
                    'language':'fa', 'sample_only':sample,
                    'audio_duration_seconds':getattr(info,'duration',None),
                    'last_transcribed_second':data[-1]['end'],
                    'processing_finished':True,
                    'coverage_notice':'پایان پردازش به معنی دریافت بی‌خطای تمام کلمات نیست؛ سکوت و گفتار تشخیص‌داده‌نشده ممکن است متن نداشته باشد.',
                    'religious_sources_verified':False, 'segments':data,
                    'notice':'متن خودکار است؛ نام‌ها، آیات و احادیث نیازمند بازشنوی و بررسی مستقل هستند. متن کتاب نیست.'}
                self.write_outputs(out, report)
                self.update(row['id'], state='sample' if sample else 'complete')
                (out/'partial.json').unlink(missing_ok=True)
                self.log(('120-second sample saved: ' if sample else 'Full transcription saved: ')+row['title'])
            except Cancelled:
                self.update(row['id'], state='interrupted', error='Stopped. Next run restarts this episode; completed episodes are kept.')
                self.log('Stopped; partial output kept separately, never marked complete.')
                break
            except Exception as exc:
                self.update(row['id'], state='error', error=str(exc)[:2000])
                self.log('FAILED: '+row['title']+' — '+str(exc))

    @staticmethod
    def write_outputs(out, report):
        prefix = 'sample' if report['sample_only'] else 'transcript'
        atomic_json(out/(prefix+'.json'), report)
        note = report['notice']
        lines = [report['title'], note, report['source_url'],
                 'فقط نمونهٔ دو دقیقه‌ای' if report['sample_only'] else 'پیاده‌سازی کل فایل ورودی؛ صحت کلمات بررسی نشده است.', '']
        srt = []
        candidates = []
        cues = ('قرآن','سوره','آیه','بخاری','مسلم','ترمذی','حدیث','ابن ماجه','ابوداود')
        for i,s in enumerate(report['segments'],1):
            line = f"[{timestamp(s['start'])}–{timestamp(s['end'])}] {s['text']}"
            lines.append(line + (' [نیازمند بازشنوی]' if s['flags'] else ''))
            srt.append(f"{i}\n{timestamp(s['start'],True)} --> {timestamp(s['end'],True)}\n{s['text']}\n")
            if any(c in s['text'] for c in cues):
                candidates.append(line)
        (out/(prefix+'.txt')).write_text('\n'.join(lines),encoding='utf-8-sig')
        (out/(prefix+'.srt')).write_text('\n'.join(srt),encoding='utf-8-sig')
        (out/(prefix+'-source-cues.txt')).write_text(
            'این‌ها فقط جمله‌های دارای کلیدواژه هستند؛ استخراج قطعی ارجاع یا تأیید حدیث نیست.\n\n'+ '\n'.join(candidates),encoding='utf-8-sig')

    def export(self):
        rows = self.rows()
        # No absolute local paths, cookies, media or downloaded models in the review export.
        index = [{k:r[k] for k in ['id','title','url','position','state','error']} for r in rows]
        atomic_json(self.root/'review-index.json', index)
        dest = self.root/'Ahsan-Qasas-Review-Export.zip'
        with zipfile.ZipFile(dest,'w',zipfile.ZIP_DEFLATED) as z:
            z.write(self.root/'review-index.json','review-index.json')
            for row in rows:
                folder = self.root/'results'/row['id']
                if not folder.is_dir():
                    continue
                for f in sorted(folder.iterdir()):
                    if row['state'] != 'complete' and f.name.startswith('transcript'):
                        continue
                    if f.suffix in {'.txt','.srt','.json'} and f.name != 'partial.json':
                        z.write(f, str(f.relative_to(self.root)))
        self.log('Export: '+str(dest))
        return dest
