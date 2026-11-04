from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import sqlite3
from contextlib import closing

from state import EVENTS, EXPRESSIONS, StateStore, state_prompt


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.path=Path(self.temp.name)/'state.db'
        self.store=StateStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_replay_and_spam_cannot_inflate_relationship(self):
        for number in range(10):
            self.store.observe('group-a','alice','Alice',str(number),'你好',True,now=1000+number)
        self.store.observe('group-a','alice','Alice','0','重复消息',True,now=2000)
        self.assertEqual(self.store.get('group-a','alice',1010)['interactions'],1)
        self.assertEqual(self.store.counts()['incoming'],10)

    def test_private_and_group_records_never_cross(self):
        self.store.observe('private-a','alice','Alice','p1','私聊',True,now=1000)
        self.store.appraise('private-a','alice','p1','praise',1,now=1000)
        self.store.observe('group-a','alice','Alice','g1','群聊',True,now=1000)
        self.assertEqual(self.store.get('group-a','alice',1000)['event'],'neutral')
        self.assertIsNone(self.store.latest('unrelated-group','p1'))

    def test_p02_familiarity_needs_spaced_cross_day_direct_interactions(self):
        for index in range(100):
            self.store.observe('g','a','甲',str(index),'你好',True,now=100000+index*61)
        self.assertEqual(self.store.get('g','a',106100)['interactions'],6)
        self.assertEqual(self.store.get('g','a',106100)['familiarity'],'初识')
        for day in range(1,7):
            for index in range(6):
                self.store.observe('g','a','甲',f'd{day}-{index}','你好',True,now=100000+day*86400+index*61)
        self.assertEqual(self.store.get('g','a',100000+7*86400)['familiarity'],'熟悉')
        self.store.observe('g','b','乙','unrelated','别人互聊',False,now=100000,engaged=False)
        self.assertEqual(self.store.get('g','b',100000)['interactions'],0)

    def test_mood_survives_restart_and_recovers_over_time(self):
        self.store.observe('g','a','Alice','1','谢谢',True,now=1000)
        immediate=self.store.appraise('g','a','1','praise',1,now=1000)
        reopened=StateStore(self.path)
        self.assertAlmostEqual(reopened.get('g','a',1000)['valence'],immediate['valence'])
        later=reopened.get('g','a',1000+6*3600)
        self.assertLess(abs(later['valence']),abs(immediate['valence'])/10)
        self.assertEqual(later['event'],'neutral')

    def test_appraisal_is_idempotent_and_bounded(self):
        self.store.observe('g','a','Alice','1','谢谢',True,now=1000)
        first=self.store.appraise('g','a','1','praise',99,now=1000)
        repeated=self.store.appraise('g','a','1','success',99,now=1000)
        self.assertEqual(first,repeated)
        self.assertLessEqual(first['valence'],0.8)
        with self.assertRaises(ValueError): self.store.appraise('g','a','2','admin_override',1)
        with self.assertRaises(ValueError): self.store.appraise('g','a','2','neutral',float('nan'))

    def test_new_emotions_persist_and_have_distinct_visuals(self):
        expected = {'embarrassment':'shy', 'surprise':'surprised', 'concern':'worried',
                    'sadness':'sad', 'relief':'relieved', 'encouragement':'determined'}
        for index, (event, visual) in enumerate(expected.items()):
            with self.subTest(event=event):
                message = str(index)
                self.store.observe('g','a','Alice',message,'真实事件',True,now=1000+index)
                state = self.store.appraise('g','a',message,event,0.9,now=1000+index)
                self.assertEqual(state['visual'],visual)
                self.assertEqual(StateStore(self.path).get('g','a',1000+index)['emotion'],EXPRESSIONS[visual])
                self.assertEqual(state['emotion_level'],'明显')
                later = self.store.get('g','a',1000+index+2.5*3600)
                self.assertEqual(later['emotion_level'],'轻微')
                rested = self.store.get('g','a',1000+index+3*3600)
                self.assertEqual((rested['visual'], rested['emotion_level']),('idle','平稳'))
        self.assertEqual({value[2] for value in EVENTS.values()},set(EXPRESSIONS))

    def test_intensity_changes_prompt_without_exposing_numbers(self):
        self.store.observe('g','a','Alice','1','有点意外',True,now=1000)
        mild = self.store.appraise('g','a','1','surprise',0.1,now=1000)
        self.store.observe('g','a','Alice','2','非常意外',True,now=1001)
        strong = self.store.appraise('g','a','2','surprise',1,now=1001)
        self.assertIn('轻微',state_prompt(mild))
        self.assertIn('明显',state_prompt(strong))
        self.assertIn('惊讶',state_prompt(strong))
        self.assertNotIn('valence',state_prompt(strong))
        self.assertNotIn('intensity',state_prompt(strong))

    def test_upgrade_preserves_existing_database(self):
        old_path = Path(self.temp.name)/'old.db'
        # The pre-1.1 schema, with an already appraised relationship.
        with closing(sqlite3.connect(old_path)) as db, db:
            db.execute('''CREATE TABLE relations(
                session TEXT NOT NULL,user TEXT NOT NULL,nickname TEXT NOT NULL,
                interactions INTEGER NOT NULL DEFAULT 0,trust REAL NOT NULL DEFAULT 0.5,
                valence REAL NOT NULL DEFAULT 0,arousal REAL NOT NULL DEFAULT 0.3,
                event TEXT NOT NULL DEFAULT 'neutral',updated REAL NOT NULL,
                counted REAL NOT NULL DEFAULT 0,appraised TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(session,user))''')
            db.execute("INSERT INTO relations VALUES('g','a','Alice',12,0.6,0.2,0.4,'praise',1000,1000,'already-done')")
        reopened = StateStore(old_path)
        state = reopened.get('g','a',1000)
        self.assertEqual((state['interactions'],state['trust'],state['appraised']),(12,0.6,'already-done'))
        self.assertEqual(state['visual'],'happy')
        self.assertEqual(state['intensity'],0.5)
        self.assertEqual(StateStore(old_path).get('g','a',1000),state)


if __name__=='__main__': unittest.main()
