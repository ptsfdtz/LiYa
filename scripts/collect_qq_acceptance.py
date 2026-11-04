"""Collect anonymous, read-only evidence from member-initiated QQ acceptance.

Receipt and persistence evidence is not a substitute for checking visible answers.
"""
import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess


def fingerprint(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


def collect(database, since=0):
    # mode=ro fails for a missing database; never instantiate ConversationStore here.
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro', uri=True, timeout=10)) as db:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        messages=db.execute('SELECT * FROM messages WHERE received>=? ORDER BY received,rowid',(since,)).fetchall()
        nickname=[]
        pictures=[]
        for row in messages:
            meta=json.loads(row['metadata'])
            if '叫我小林' in row['text']:
                preference=db.execute('''SELECT 1 FROM preferences WHERE session=? AND user=?
                    AND key='nickname' AND value='小林' AND source_id=?''',
                    (row['session'],row['user'],row['id'])).fetchone()
                receipt=db.execute("SELECT result FROM operations WHERE session=? AND request_id=? AND kind='reply-body'",
                                   (row['session'],row['id'])).fetchone()
                reply_status=json.loads(receipt['result']).get('status') if receipt else 'unobserved'
                nickname.append({'source_fingerprint':fingerprint(row['id']),
                                 'preference_currently_saved':bool(preference),'reply_status':reply_status})
            if meta.get('picture') or meta.get('emoji') or any(k in meta.get('media',[]) for k in ('image','emoji')):
                pictures.append({'source_fingerprint':fingerprint(row['id']),
                                 'picture':bool(meta.get('picture') or 'image' in meta.get('media',[])),
                                 'emoji':bool(meta.get('emoji') or 'emoji' in meta.get('media',[]))})
        reminders=[]
        for row in db.execute('''SELECT r.* FROM reminders r JOIN messages m
            ON m.session=r.session AND m.id=r.source_id
            WHERE m.received>=? AND instr(m.text,'提醒我交作业')>0 ORDER BY r.due''',(since,)):
            sent_receipt=row['status']=='sent' and bool(row['detail'])
            reminders.append({'reminder_fingerprint':fingerprint(row['id']),'due':row['due'],
                              'status':row['status'],'has_platform_receipt':sent_receipt,
                              'receipt_fingerprint':fingerprint(row['detail']) if sent_receipt else None})
        return {'checked_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),
                'since':since,'received_message_count':len(messages),
                'latest_received':max((r['received'] for r in messages),default=None),
                'nickname_test_evidence':nickname,'visual_inbound_evidence':pictures,
                'reminder_test_evidence':reminders,'completion_proven':False,
                'scope':'Only reception, persistence and recorded send receipts. Visible answer quality, ownership and passive/active QQ delivery conditions still require review.'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',default='/opt/dragon-maibot/data/plugins/local.liya-companion/conversation.sqlite3')
    parser.add_argument('--since',type=float,default=0)
    parser.add_argument('--remote',action='store_true')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.remote:
        source=Path(__file__).read_text(encoding='utf-8')
        result=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','marsIntranet',
            'python3','-','--database',args.database,'--since',str(args.since)],
            input=source,text=True,encoding='utf-8',capture_output=True,check=True)
        report=json.loads(result.stdout)
    else:
        report=collect(args.database,args.since)
    serialized=json.dumps(report,ensure_ascii=False,indent=2)+'\n'
    if args.output:
        args.output.write_text(serialized,encoding='utf-8')
    else:
        print(serialized,end='')


if __name__=='__main__': main()
