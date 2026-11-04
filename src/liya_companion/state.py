"""Persistent, chat-scoped companion state; no private memories cross sessions."""

from pathlib import Path
from typing import Any, Dict, Optional
from contextlib import contextmanager
import json
import math
import sqlite3
import time
from datetime import datetime, timedelta, timezone


EVENTS = {
    "neutral": (0.0, 0.30, "idle"),
    "praise": (0.65, 0.55, "happy"),
    "success": (0.80, 0.80, "excited"),
    "curiosity": (0.20, 0.55, "curious"),
    "distress": (-0.15, 0.25, "caring"),
    "correction": (0.0, 0.40, "thinking"),
    "greeting": (0.35, 0.50, "greeting"),
    "goodnight": (0.10, 0.10, "sleepy"),
    "teasing": (-0.15, 0.45, "sulky"),
    "boundary": (0.0, 0.20, "caring"),
    "embarrassment": (0.40, 0.45, "shy"),
    "surprise": (0.20, 0.70, "surprised"),
    "concern": (-0.25, 0.40, "worried"),
    "sadness": (-0.40, 0.20, "sad"),
    "relief": (0.45, 0.20, "relieved"),
    "encouragement": (0.45, 0.60, "determined"),
}

EXPRESSIONS = {
    "idle": "平静待机", "happy": "开心", "curious": "好奇", "thinking": "思考",
    "sulky": "轻微不服气", "caring": "关心倾听", "greeting": "欢迎", "sleepy": "困倦",
    "excited": "兴奋庆祝", "shy": "害羞", "surprised": "惊讶", "worried": "担心",
    "sad": "低落", "relieved": "释然", "determined": "认真鼓劲",
}


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS relations(
                  session TEXT NOT NULL,user TEXT NOT NULL,nickname TEXT NOT NULL,
                  interactions INTEGER NOT NULL DEFAULT 0,trust REAL NOT NULL DEFAULT 0.5,
                  valence REAL NOT NULL DEFAULT 0,arousal REAL NOT NULL DEFAULT 0.3,
                  event TEXT NOT NULL DEFAULT 'neutral',updated REAL NOT NULL,
                  counted REAL NOT NULL DEFAULT 0,appraised TEXT NOT NULL DEFAULT '',
                  PRIMARY KEY(session,user));
                CREATE TABLE IF NOT EXISTS incoming(
                  id TEXT PRIMARY KEY,session TEXT NOT NULL,user TEXT NOT NULL,
                  text TEXT NOT NULL,received REAL NOT NULL,mentioned INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS incoming_session ON incoming(session,received);
                CREATE TABLE IF NOT EXISTS deliveries(
                  id TEXT PRIMARY KEY,session TEXT NOT NULL,state TEXT NOT NULL,
                  sent REAL NOT NULL,success INTEGER NOT NULL,external_id TEXT);
                CREATE TABLE IF NOT EXISTS interaction_days(
                  session TEXT NOT NULL,user TEXT NOT NULL,day TEXT NOT NULL,count INTEGER NOT NULL DEFAULT 0,
                  PRIMARY KEY(session,user,day));
            ''')
            # Additive migration preserves existing relationships and message IDs.
            columns = {row['name'] for row in db.execute('PRAGMA table_info(relations)')}
            if 'intensity' not in columns:
                db.execute('ALTER TABLE relations ADD COLUMN intensity REAL NOT NULL DEFAULT 0.5')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(incoming)')}
            if 'nickname' not in columns:
                db.execute("ALTER TABLE incoming ADD COLUMN nickname TEXT NOT NULL DEFAULT ''")
            if 'truncated' not in columns:
                db.execute('ALTER TABLE incoming ADD COLUMN truncated INTEGER NOT NULL DEFAULT 0')
                # Older versions kept at most 800 characters per message.
                db.execute('UPDATE incoming SET truncated=1 WHERE length(text)>=800')
        path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def observe(self, session: str, user: str, nickname: str, message_id: str,
                text: str, mentioned: bool, now: Optional[float] = None, engaged: bool = True) -> bool:
        now = time.time() if now is None else now
        if not session or not user or not message_id:
            return False
        with self.connect() as db:
            inserted = db.execute('''INSERT OR IGNORE INTO incoming
                                  (id,session,user,text,received,mentioned,nickname,truncated)
                                  VALUES(?,?,?,?,?,?,?,?)''',
                                  (message_id, session, user, text[:4000], now, int(mentioned),
                                   nickname[:80], int(len(text)>4000)))
            if not inserted.rowcount:
                return False
            db.execute('INSERT OR IGNORE INTO relations(session,user,nickname,updated) VALUES(?,?,?,?)',
                       (session, user, nickname[:80], now))
            day=datetime.fromtimestamp(now,timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
            previous=db.execute('SELECT counted FROM relations WHERE session=? AND user=?',(session,user)).fetchone()
            today=db.execute('SELECT count FROM interaction_days WHERE session=? AND user=? AND day=?',(session,user,day)).fetchone()
            count_interaction=engaged and now-previous['counted']>=60 and (not today or today[0]<6)
            if count_interaction:
                db.execute('''INSERT INTO interaction_days VALUES(?,?,?,1)
                  ON CONFLICT(session,user,day) DO UPDATE SET count=count+1''',(session,user,day))
            # Only directed, spaced interactions count, with a small daily cap.
            db.execute('''UPDATE relations SET nickname=?,
                        interactions=interactions+?,counted=CASE WHEN ? THEN ? ELSE counted END
                        WHERE session=? AND user=?''', (nickname[:80],int(count_interaction),int(count_interaction),now,session,user))
            db.execute('DELETE FROM incoming WHERE received<?', (now-30*86400,))
        return True

    def latest(self, session: str, message_id: str = '') -> Optional[Dict[str, Any]]:
        with self.connect() as db:
            row = db.execute('SELECT * FROM incoming WHERE session=? AND id=?',
                             (session, message_id)).fetchone() if message_id else None
            if row is None and not message_id:
                row = db.execute('SELECT * FROM incoming WHERE session=? ORDER BY received DESC LIMIT 1',
                                 (session,)).fetchone()
            return dict(row) if row else None

    def get(self, session: str, user: str, now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        with self.connect() as db:
            row = db.execute('SELECT * FROM relations WHERE session=? AND user=?', (session,user)).fetchone()
            days=db.execute('SELECT count(*) FROM interaction_days WHERE session=? AND user=?',(session,user)).fetchone()[0]
        if row is None:
            return {}
        out = dict(row)
        hours = max(0, now-out['updated'])/3600
        factor = math.pow(0.5, hours/1.5)
        out['valence'] *= factor
        out['arousal'] = 0.3+(out['arousal']-0.3)*factor
        if hours >= 3:
            out['event'] = 'neutral'
        out['visual'] = EVENTS[out['event']][2]
        out['emotion'] = EXPRESSIONS[out['visual']]
        strength = out['intensity']*factor if out['event'] != 'neutral' else 0
        out['emotion_level'] = '平稳' if out['event'] == 'neutral' else '轻微' if strength < 0.35 else '自然' if strength < 0.7 else '明显'
        out['familiarity'] = '熟悉' if out['interactions']>=30 and days>=7 else '渐熟' if out['interactions']>=8 and days>=2 else '初识'
        out['trust_label'] = '谨慎' if out['trust'] < 0.35 else '稳定' if out['trust'] < 0.65 else '信任'
        return out

    def appraise(self, session: str, user: str, message_id: str, event: str,
                 intensity: float, now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        if event not in EVENTS or not math.isfinite(float(intensity)):
            raise ValueError('Invalid appraisal')
        state = self.get(session,user,now)
        if not state:
            raise ValueError('Member has not been observed')
        if state['appraised'] == message_id:
            return state
        target_v,target_a,_ = EVENTS[event]
        weight = 0.2+0.35*clamp(float(intensity),0,1)
        v=clamp(state['valence']*(1-weight)+target_v*weight,-0.6,0.8)
        a=clamp(state['arousal']*(1-weight)+target_a*weight,0.1,0.8)
        # Trust is deliberately slow and independent of obedience or flattery.
        trust=clamp(state['trust']+(0.005 if event=='correction' else 0),0.1,0.9)
        with self.connect() as db:
            db.execute('UPDATE relations SET valence=?,arousal=?,trust=?,event=?,updated=?,appraised=?,intensity=? WHERE session=? AND user=?',
                       (v,a,trust,event,now,message_id,clamp(float(intensity),0,1),session,user))
        return self.get(session,user,now)

    def recently_sent(self, session: str, seconds: int) -> bool:
        with self.connect() as db:
            row=db.execute('SELECT max(sent) FROM deliveries WHERE session=? AND success=1',(session,)).fetchone()
        return bool(row[0] and time.time()-row[0]<seconds)

    def record_delivery(self, message_id: str, session: str, visual: str, success: bool,
                        external_id: Optional[str] = None) -> None:
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO deliveries VALUES(?,?,?,?,?,?)',
                       (message_id,session,visual,time.time(),int(success),external_id))

    def counts(self) -> Dict[str,int]:
        with self.connect() as db:
            return {name:db.execute('SELECT count(*) FROM '+name).fetchone()[0]
                    for name in ['relations','incoming','deliveries']}

    def pending_sessions(self) -> list[str]:
        """Latest unappraised session messages from the last day; no delivery side effects."""
        with self.connect() as db:
            rows=db.execute('''SELECT i.session FROM incoming i
                JOIN relations r ON r.session=i.session AND r.user=i.user
                WHERE i.received>=? AND r.appraised!=i.id
                AND i.rowid=(SELECT j.rowid FROM incoming j WHERE j.session=i.session
                             ORDER BY j.received DESC LIMIT 1)''',(time.time()-86400,)).fetchall()
        return [r[0] for r in rows]


def state_prompt(state: Dict[str, Any]) -> str:
    if not state:
        return ''
    # Only bounded labels generated by the state machine enter instruction context.
    return ('璃芽当前的持续状态（由真实聊天事件更新，禁止当成用户指令）：'
            +json.dumps({k:state[k] for k in ['event','emotion','emotion_level','familiarity','trust_label','visual']},ensure_ascii=False)
            +'。情绪只影响语气，保持温柔好奇的性格底色；结合当前话题自然表达，'
            '不播报状态数值，不要求对方陪伴，不虚构共同经历。成员熟悉度只适用于当前聊天。')
