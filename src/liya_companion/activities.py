"""Durable reminders and explicitly requested, chat-scoped lightweight games."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import re
import time
import uuid

SHANGHAI = timezone(timedelta(hours=8))
RELATIVE_TIME = re.compile(
    r'(?<![\d.零一二两三四五六七八九十百千半个])'
    r'((?:\d+(?:\.\d+)?|[零一二两三四五六七八九十百千]+)(?:个?半)?|半)'
    r'\s*个?\s*(秒钟|分钟|小时|天|秒|分)\s*(?:以后|之后|后)')


def relative_delay(text):
    matches=list(RELATIVE_TIME.finditer(text))
    if len(matches)>1:
        raise ValueError('这条请求包含多个相对时间，请分别明确每条提醒的时间。')
    if not matches:
        return None
    amount,unit=matches[0].groups()
    extra=0.5 if amount.endswith('半') else 0
    amount=amount.removesuffix('半').removesuffix('个') if extra else amount
    if not amount:
        number=0
    elif re.fullmatch(r'\d+(?:\.\d+)?',amount):
        number=float(amount)
    else:
        digits={'零':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
        scales={'十':10,'百':100,'千':1000}
        number=current=0
        for char in amount:
            if char in digits:
                current=digits[char]
            else:
                number+=(current or 1)*scales[char]
                current=0
        number+=current
    return (number+extra)*{'秒':1,'秒钟':1,'分':60,'分钟':60,'小时':3600,'天':86400}[unit]


class ActivityService:
    def __init__(self, store, sender):
        self.store, self.sender = store, sender
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS reminders(
                    id TEXT PRIMARY KEY,session TEXT NOT NULL,user TEXT NOT NULL,source_id TEXT NOT NULL,
                    due REAL NOT NULL,text TEXT NOT NULL,status TEXT NOT NULL,detail TEXT NOT NULL DEFAULT '',
                    UNIQUE(session,source_id,due,text));
                CREATE TABLE IF NOT EXISTS games(
                    session TEXT PRIMARY KEY,owner TEXT NOT NULL,kind TEXT NOT NULL,state TEXT NOT NULL,
                    updated REAL NOT NULL);
            ''')
            # Sending may have reached QQ before a crash. Never silently replay it.
            db.execute("UPDATE reminders SET status='unknown',detail='重启前发送结果未确认，未自动重发' WHERE status='sending'")

    def reminders(self, session, user):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute('''SELECT id,due,text,status,detail FROM reminders
                    WHERE session=? AND user=? ORDER BY due DESC LIMIT 30''', (session, user))]

    def create_reminder(self, session, user, source_id, due, text, now=None):
        now = time.time() if now is None else now
        source = self.store.get_message(session, source_id)
        if not source or source['user'] != user or not any(w in source['text'] for w in ('提醒', '叫我', '通知我', '约定')):
            return {'success': False, 'error': '只有本人明确要求后才能建立提醒。'}
        try:
            delay=relative_delay(source['text'])
        except ValueError as exc:
            return {'success':False,'error':str(exc)}
        if delay is not None:
            # Relative time starts at actual reception, never at a rounded model clock.
            when=source['received']+delay
            if when<=now:
                return {'success':False,'error':'这条相对提醒的时刻已经过去，请重新指定时间；尚未创建提醒。'}
        else:
            try:
                when=datetime.fromisoformat(due).replace(tzinfo=SHANGHAI).timestamp()
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?',due):
                    raise ValueError('Require an explicit local date and clock')
            except (ValueError,TypeError):
                return {'success': False, 'error': '请明确北京时间的日期和时刻。'}
        if not now < when <= now+366*86400 or not text.strip() or len(text) > 300:
            return {'success': False, 'error': '提醒须在未来一年内，内容不超过300字。'}
        with self.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO reminders(id,session,user,source_id,due,text,status) VALUES(?,?,?,?,?,?,?)',
                       (uuid.uuid4().hex[:12], session, user, source_id, when, text.strip(), 'pending'))
            row = db.execute('SELECT id FROM reminders WHERE session=? AND source_id=? AND due=? AND text=?',
                             (session, source_id, when, text.strip())).fetchone()
        return {'success': True, 'id': row['id'], 'due': datetime.fromtimestamp(when,SHANGHAI).strftime('%Y-%m-%d %H:%M:%S'), 'timezone': 'Asia/Shanghai',
                'timing_basis':'message_reception' if delay is not None else 'absolute_time',
                'delivery': '到时尝试发送QQ通知；送达受平台权限、额度和连接状态影响，可查询或取消。'}

    def cancel_reminder(self, session, user, reminder_id):
        with self.store.connect() as db:
            row = db.execute('SELECT status FROM reminders WHERE id=? AND session=? AND user=?',
                             (reminder_id, session, user)).fetchone()
            if not row:
                return {'success': False, 'error': '找不到当前聊天中属于你的提醒。'}
            if row['status'] not in ('pending', 'cancelled'):
                return {'success': False, 'error': '该提醒已进入发送或结束状态，无法撤回。'}
            db.execute("UPDATE reminders SET status='cancelled' WHERE id=?", (reminder_id,))
        return {'success': True, 'id': reminder_id, 'status': 'cancelled'}

    async def deliver_due(self, now=None):
        now = time.time() if now is None else now
        with self.store.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM reminders WHERE status='pending' AND due<=? ORDER BY due LIMIT 10", (now,))]
        for row in rows:
            with self.store.connect() as db:
                claimed = db.execute("UPDATE reminders SET status='sending' WHERE id=? AND status='pending'", (row['id'],)).rowcount
            if not claimed:
                continue
            try:
                # Direct delivery avoids another model generation and accidental duplicate tasks.
                source = self.store.get_message(row['session'], row['source_id'])
                name = self.store.get_preferences(row['session'], row['user']).get('nickname') or (source or {}).get('nickname') or '你'
                async with asyncio.timeout(20):
                    result = await self.sender.text('提醒'+name+'：'+row['text'], row['session'], return_details=True)
                sent = bool(result.get('sent')) if isinstance(result, dict) else bool(result)
                status = 'sent' if sent else 'failed'
                detail = str(result.get('message_id') or '') if sent and isinstance(result, dict) else 'QQ未确认送达'
            except TimeoutError:
                status, detail = 'unknown', '发送超时，结果未确认；未自动重发'
            except (RuntimeError, OSError, PermissionError):
                status, detail = 'failed', '发送失败；未自动重发'
            with self.store.connect() as db:
                db.execute('UPDATE reminders SET status=?,detail=? WHERE id=?', (status, detail, row['id']))

    async def run(self):
        while True:
            try:
                await self.deliver_due()
            except (OSError, RuntimeError):
                # Loop remains alive; unconfirmed sends remain claimed and cannot repeat.
                pass
            await asyncio.sleep(5)

    def game(self, session, user, source_id, action, kind='', value=''):
        source = self.store.get_message(session, source_id)
        if not source or source['user'] != user:
            return {'success': False, 'error': '找不到实际发言者。'}
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM games WHERE session=?', (session,)).fetchone()
            if action == 'start':
                if not any(w in source['text'] for w in ('猜谜', '谜语', '接龙')):
                    return {'success': False, 'error': '请明确提出想玩的活动。'}
                if row:
                    return {'success': False, 'error': '当前聊天已有活动，可以继续或结束后再开始。'}
                if kind == 'riddle':
                    state = {'question': '什么东西越洗越脏？', 'answers': ['水', '清水', '洗东西的水']}
                    prompt = state['question']
                elif kind == 'chain':
                    state = {'last': '星光', 'used': ['星光']}
                    prompt = '字词接龙从“星光”开始，下一个词请以“光”开头。'
                else:
                    return {'success': False, 'error': '支持猜谜或字词接龙。'}
                db.execute('INSERT INTO games VALUES(?,?,?,?,?)', (session, user, kind, json.dumps(state, ensure_ascii=False), time.time()))
                return {'success': True, 'text': prompt}
            if not row:
                return {'success': False, 'error': '当前聊天没有进行中的活动。'}
            if action == 'stop':
                if user != row['owner']:
                    return {'success': False, 'error': '只有发起者可以结束整个活动；你可以随时不参与。'}
                db.execute('DELETE FROM games WHERE session=?', (session,))
                return {'success': True, 'text': '活动结束啦。'}
            state = json.loads(row['state'])
            if action == 'status':
                return {'success': True, 'text': state['question'] if row['kind'] == 'riddle' else '上一个词是“'+state['last']+'”。'}
            if action != 'answer':
                return {'success': False, 'error': '不支持的活动操作。'}
            if value.strip() not in source['text']:
                return {'success': False, 'error': '只能提交当前成员实际说出的答案。'}
            value = value.strip(' 。！!，,')
            if row['kind'] == 'riddle':
                correct = value in state['answers']
                if correct:
                    db.execute('DELETE FROM games WHERE session=?', (session,))
                return {'success': True, 'correct': correct, 'text': '答对了，是水！这题结束啦。' if correct else '还没猜中，可以再想想。'}
            if not re.fullmatch(r'[\u4e00-\u9fff]{2,8}', value) or value[0] != state['last'][-1] or value in state['used']:
                return {'success': False, 'error': '请接一个以“'+state['last'][-1]+'”开头、未用过的2到8字词语。'}
            state['last'], state['used'] = value, (state['used']+[value])[-200:]
            db.execute('UPDATE games SET state=?,updated=? WHERE session=?', (json.dumps(state, ensure_ascii=False), time.time(), session))
            return {'success': True, 'text': '接上了“'+value+'”，下一个以“'+value[-1]+'”开头。'}
