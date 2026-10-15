"""Private, owner-paired Telegram front end for the local free speech collector."""
import getpass
import json
import os
from pathlib import Path
import secrets
import threading
import time
import requests
from agent import Agent, atomic_json
from cloud import youtube_url

BASE=Path(__file__).resolve().parent/'work'/'telegram'

def acquire_lock():
    BASE.mkdir(parents=True,exist_ok=True)
    handle=(BASE/'bot.lock').open('a+b')
    handle.seek(0); handle.write(b'0'); handle.flush(); handle.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError('Another copy of this bot is already running.')
    return handle

class Bot:
    def __init__(self,token,root=BASE):
        self.token=token
        self.root=Path(root)
        self.root.mkdir(parents=True,exist_ok=True)
        self.settings=self.root/'settings.json'
        self.config=json.loads(self.settings.read_text()) if self.settings.exists() else {}
        self.code=secrets.token_hex(4)
        self.stop=threading.Event()
        self.busy=threading.Event()
        self.mutex=threading.Lock()
        self.status='آماده'
        self.agent=Agent(self.root/'queue',log=self.log,stop=self.stop,seed=False)

    def api(self,method,data=None,files=None,timeout=45):
        # Never print HTTP exceptions: their URLs include the bot token.
        r=requests.post('https://api.telegram.org/bot'+self.token+'/'+method,
            data=data or {},files=files,timeout=timeout)
        r.raise_for_status()
        payload=r.json()
        if not payload.get('ok'): raise RuntimeError('Telegram rejected the request')
        return payload['result']

    def save(self): atomic_json(self.settings,self.config)

    def say(self,text,chat=None):
        try: self.api('sendMessage',{'chat_id':chat or self.config['owner'],'text':text[:3900]})
        except Exception: print('Telegram message could not be delivered; local results are retained.',flush=True)

    def document(self,path):
        if path.stat().st_size>45_000_000:
            self.say('فایل از سقف ارسال این ربات بزرگ‌تر است؛ در لپ‌تاپ ذخیره شد: '+path.name)
            return
        for attempt in range(2):
            try:
                with path.open('rb') as f:
                    self.api('sendDocument',{'chat_id':self.config['owner']},
                        {'document':(path.name,f)},timeout=120)
                return
            except Exception:
                if attempt: self.say('ارسال فایل موفق نشد؛ خروجی در لپ‌تاپ محفوظ است. با /export دوباره بگیر.')

    def log(self,text):
        self.status=text[:500]
        print(text,flush=True)

    def handle(self,update):
        m=update.get('message',{})
        chat=m.get('chat',{})
        if chat.get('type')!='private': return
        chat_id=chat.get('id')
        text=m.get('text','').strip()
        if not self.config.get('owner'):
            if text!='/start '+self.code: return
            self.config['owner']=chat_id; self.save()
            self.say('وصل شد ✅ لینک ویدیو یا پلی‌لیست را بفرست. /help برای راهنما.')
            return
        if chat_id!=self.config['owner']: return
        if text in {'/start','/help'}:
            self.say('لینک یوتیوب را بفرست: دریافت صوت و تبدیل کامل گفتار شروع می‌شود.\n/status وضعیت\n/stop توقف\n/continue ادامهٔ صف\n/export دریافت متن‌ها\n\nمدل medium رایگان است و زمان می‌برد. لپ‌تاپ باید روشن بماند. متن خودکار نیازمند بررسی است؛ ربات کتاب نمی‌نویسد و تصاویر را تحلیل نمی‌کند.')
        elif text=='/status':
            rows=self.agent.rows()
            self.say(f"کامل: {sum(r['state']=='complete' for r in rows)} از {len(rows)}\n"+self.status)
        elif text=='/stop':
            self.stop.set();self.say('درخواست توقف ثبت شد؛ دانلود یا بارگیری مدل ممکن است فوراً قطع نشود.')
        elif text=='/export':
            if self.busy.is_set(): self.say('در حال پردازش هستم؛ خروجی هر قسمت پس از پایان ارسال می‌شود. برای خروجی کلی ابتدا /stop بزن.')
            else: self.start_job(None,export_only=True)
        elif text=='/continue': self.start_job(None)
        else:
            try: url=youtube_url(text)
            except (ValueError,TypeError):
                self.say('یک لینک معتبر https یوتیوب یا /help بفرست.');return
            self.start_job(url)

    def start_job(self,url,export_only=False):
        with self.mutex:
            if self.busy.is_set():
                self.say('صف فعلی در حال اجراست؛ /status وضعیت را نشان می‌دهد.');return
            self.busy.set();self.stop.clear()
        self.say('دریافت درخواست ✅ پردازش آغاز شد.' if not export_only else 'خروجی آماده می‌شود.')
        threading.Thread(target=self.work,args=(url,export_only),daemon=True).start()

    def work(self,url,export_only=False):
        try:
            if export_only:
                self.document(self.agent.export());return
            if url:
                if 'playlist?' in url:
                    n=self.agent.discover(url)
                    self.say(f'فهرست دریافت شد: {n} مورد. این عدد به معنی دریافت گفتار نیست.')
                else:
                    from urllib.parse import urlsplit,parse_qs
                    vid=parse_qs(urlsplit(url).query)['v'][0]
                    with self.agent.db() as c:
                        c.execute('INSERT OR IGNORE INTO videos(id,title,url,position,updated) VALUES(?,?,?,?,?)',
                            (vid,vid,url,99999,time.time()))
            rows=self.agent.rows()
            if not rows: self.say('صف خالی است؛ ابتدا لینک بفرست.');return
            selected={r['id'] for r in rows} if not url or 'playlist?' in url else {vid}
            for row in rows:
                if self.stop.is_set(): break
                if row['id'] not in selected or row['state']=='complete': continue
                self.say('در حال دریافت صوت: '+row['title'])
                media=self.agent.audio(row,True)
                self.document(media)
                self.say('در حال تبدیل گفتار: '+row['title']+'\nمدل medium؛ دفعهٔ اول دانلود مدل هم لازم است.')
                self.agent.process([row['id']],model='medium',sample=False,allow_download=True)
                current=next(r for r in self.agent.rows() if r['id']==row['id'])
                if current['state']=='complete':
                    self.say('گفتار این قسمت آماده شد؛ صحت متن هنوز بررسی نشده است.')
                    self.document(self.agent.root/'results'/row['id']/'transcript.txt')
                    self.document(self.agent.root/'results'/row['id']/'transcript.srt')
                    self.agent.export()
                elif current['state']=='error':
                    self.say('تبدیل این قسمت خطا داد؛ صف محفوظ است. برای بررسی، خروجی /export را بگیر.');break
            self.document(self.agent.export())
            self.say('این نوبت متوقف یا تمام شد. /status تعداد قسمت‌های کامل را نشان می‌دهد؛ /continue ادامه می‌دهد.')
        except Exception:
            self.say('دریافت یا پردازش با خطا متوقف شد. نتایج قبلی محفوظ‌اند. اگر یوتیوب دانلود را مسدود کرده باشد، ربات آن محدودیت را رفع نمی‌کند.')
        finally:
            self.busy.clear()

    def run(self):
        try:
            me=self.api('getMe')
            info=self.api('getWebhookInfo')
            if info.get('url'):
                print('This bot has an existing webhook. Use a new dedicated bot; no webhook was changed.')
                return
        except Exception:
            print('Cannot connect to Telegram. Check the token and internet. Token was not printed.')
            return
        print('Telegram bot: https://t.me/'+me['username'],flush=True)
        if not self.config.get('owner'):
            print('Send this exact message in a PRIVATE chat with your bot: /start '+self.code,flush=True)
        else: print('Paired owner restored. Send /help in Telegram.',flush=True)
        while True:
            try:
                updates=self.api('getUpdates',{'offset':self.config.get('offset',0),'timeout':25,
                    'allowed_updates':json.dumps(['message'])},timeout=35)
                for u in updates:
                    self.handle(u)
                    self.config['offset']=u['update_id']+1;self.save()
            except KeyboardInterrupt:
                self.stop.set()
                while self.busy.is_set(): time.sleep(.25)
                return
            except Exception:
                print('Telegram connection interrupted; retrying in 5 seconds.',flush=True)
                time.sleep(5)

if __name__=='__main__':
    lock=acquire_lock()
    token=os.environ.get('TELEGRAM_BOT_TOKEN') or getpass.getpass('Paste BotFather token (hidden), then Enter: ').strip()
    if not token: raise SystemExit('No token entered.')
    Bot(token).run()
