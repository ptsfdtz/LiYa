"""Do not turn missing, partial or ambiguous live evidence into completed acceptance."""
import importlib.util
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

spec=importlib.util.spec_from_file_location('qq_acceptance_collector',Path(__file__).resolve().parents[1]/'scripts/collect_qq_acceptance.py')
collector=importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


class CollectorTests(unittest.TestCase):
    def test_missing_database_is_not_created(self):
        with TemporaryDirectory() as temporary:
            path=Path(temporary)/'missing.sqlite3'
            with self.assertRaises(sqlite3.OperationalError): collector.collect(path)
            self.assertFalse(path.exists())

    def test_partial_receipts_are_not_completion_and_original_rows_are_unchanged(self):
        with TemporaryDirectory() as temporary:
            path=Path(temporary)/'test.sqlite3'
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript('''
                    CREATE TABLE messages(session TEXT,id TEXT,user TEXT,text TEXT,metadata TEXT,received REAL);
                    CREATE TABLE preferences(session TEXT,user TEXT,key TEXT,value TEXT,source_id TEXT);
                    CREATE TABLE operations(session TEXT,request_id TEXT,kind TEXT,result TEXT);
                    CREATE TABLE reminders(id TEXT,session TEXT,user TEXT,source_id TEXT,due REAL,status TEXT,detail TEXT);
                ''')
                db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)',('private-group-id','source-user-message','private-user-id','以后叫我小林','{}',100))
                db.execute('INSERT INTO preferences VALUES(?,?,?,?,?)',('private-group-id','private-user-id','nickname','小林','source-user-message'))
                db.execute('INSERT INTO operations VALUES(?,?,?,?)',('private-group-id','source-user-message','reply-body','{"status":"sending"}'))
                db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)',('private-group-id','pic','private-user-id','private text',json.dumps({'media':['image']}),101))
                db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)',('private-group-id','reminder','private-user-id','一分钟后提醒我交作业','{}',102))
                db.execute('INSERT INTO reminders VALUES(?,?,?,?,?,?,?)',('private-reminder-id','private-group-id','private-user-id','reminder',200,'unknown','private unconfirmed detail'))
            before=path.read_bytes()
            result=collector.collect(path,since=99)
            self.assertFalse(result['completion_proven'])
            self.assertTrue(result['nickname_test_evidence'][0]['preference_currently_saved'])
            self.assertEqual(result['nickname_test_evidence'][0]['reply_status'],'sending')
            self.assertFalse(result['reminder_test_evidence'][0]['has_platform_receipt'])
            self.assertTrue(result['visual_inbound_evidence'][0]['picture'])
            self.assertNotIn('private-',json.dumps(result))
            self.assertNotIn('private text',json.dumps(result))
            self.assertEqual(before,path.read_bytes())
            self.assertEqual(collector.collect(path,since=103)['received_message_count'],0)


if __name__=='__main__': unittest.main()
