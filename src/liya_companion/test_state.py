from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from state import StateStore


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


if __name__=='__main__': unittest.main()
