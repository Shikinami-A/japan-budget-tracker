"""Guard against mistaken party assignments in public political data."""
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from party_sources import matching_members, apply_party_rosters


def member(name,reading,chamber='衆議院',caucus='自民'):
    return dict(id=chamber+'-'+name,name=name,reading=reading,chamber=chamber,
                party=None,party_source=None,caucus=caucus)


class PartyJoinTests(unittest.TestCase):
    def test_homonyms_require_chamber(self):
        people=[member('同名議員','どうめいぎいん'),member('同名議員','どうめいぎいん','参議院')]
        self.assertEqual(matching_members(('同名議員',None,None),people),[])
        self.assertEqual(matching_members(('同名議員',None,'参議院議員'),people),[people[1]])

    def test_kana_alias_is_unique_full_reading(self):
        people=[member('柏倉祐司','かしわくらゆうじ')]
        self.assertEqual(matching_members(('柏倉ゆうじかしわくらゆうじ',None,'衆議院議員'),people),people)
        self.assertEqual(matching_members(('柏倉','ゆうじ','衆議院議員'),people),[])
        ambiguous=people+[member('別表記','かしわくらゆうじ')]
        self.assertEqual(matching_members(('柏倉ゆうじかしわくらゆうじ',None,'衆議院議員'),ambiguous),[])

    def test_short_name_does_not_match_longer_name(self):
        self.assertEqual(matching_members(('佐藤健太',None,'衆議院議員'),[member('佐藤健','さとうけん')]),[])

    def test_conflicting_rosters_are_not_resolved_by_caucus(self):
        people=[member('浮島智子','うきしまともこ',caucus='中道')]
        apply_party_rosters(people,ROOT/'data/source-text',lambda *a,**kw:None,'2026-10-08')
        self.assertIsNone(people[0]['party'])
        self.assertEqual(people[0]['party_status'],'資料間不一致')
        self.assertEqual({e['party'] for e in people[0]['party_evidence']},{'公明党','中道改革連合'})
        self.assertTrue(all(e['as_of'] is None for e in people[0]['party_evidence']))

    def test_unmatched_caucus_never_fills_party(self):
        people=[member('資料なし議員','しりょうなしぎいん')]
        apply_party_rosters(people,ROOT/'data/source-text',lambda *a,**kw:None,'2026-10-08')
        self.assertIsNone(people[0]['party'])
        self.assertEqual(people[0]['party_status'],'未照合')


if __name__=='__main__':unittest.main()
