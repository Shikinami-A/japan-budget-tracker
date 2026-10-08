"""Join reviewed party rosters; a caucus label is never a party assignment."""
import re
import json
import hashlib
from urllib.parse import urlsplit
import unicodedata

PARTIES = {
    'ishin': ('日本維新の会', 'https://o-ishin.jp/member/'),
    'dpfp': ('国民民主党', 'https://new-kokumin.jp/member'),
    'craj': ('中道改革連合', 'https://craj.jp/members'),
    'cdp': ('立憲民主党', 'https://cdp-japan.jp/members'),
    'jcp': ('日本共産党', 'https://www.jcp.or.jp/diet_member/'),
    'reiwa': ('れいわ新選組', 'https://reiwa-shinsengumi.com/member/'),
    'hoshuto': ('日本保守党', 'https://hoshuto.jp/member/parliament/'),
    'mirai': ('チームみらい', 'https://team-mir.ai/about'),
    'komei': ('公明党', 'https://www.komei.or.jp/member/result/'),
}


def compact(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value or ''))


def entries(ident, text):
    if ident == 'dpfp':
        return [(name, reading, chamber) for name, reading, chamber in re.findall(
            r'^- (.+)\n: (.+)\n\n(衆議院議員|参議院議員)', text, re.M)]
    if ident == 'craj':
        return [(name, reading, '衆議院議員') for name, reading in re.findall(
            r'^- [^\n]+\n- ([^\n]+)\n- ([^\n]+)\n- 衆議院議員', text, re.M)]
    if ident == 'ishin':
        return [(name, None, chamber) for name, chamber in re.findall(
            r'^([^\n]+)\n: - (衆議院議員|参議院議員)', text, re.M)]
    if ident == 'cdp':
        return [(name, None, '参議院議員') for name in re.findall(
            r'^### ([^\n]+)\n\n参議院議員', text, re.M)]
    if ident == 'jcp':
        # The selected excerpt retains name/reading pairs only. Cross-chamber
        # homonyms are rejected by the uniqueness check below.
        return [(name, reading, None) for name, reading in re.findall(
            r'^([^#\n][^\n]+)\n([^\n]+)(?:\n|$)', text.split('## 国会議員')[1], re.M)]
    if ident == 'reiwa':
        return [(name.strip(), None, chamber) for chamber, name in re.findall(
            r'^(衆議院議員|参議院議員) ([^（\n]+)', text, re.M)]
    if ident == 'hoshuto':
        return [(name, reading, '参議院議員') for name, reading in re.findall(
            r'^([^\n]+?) （([^）]+)） 全国比例', text, re.M)]
    if ident == 'mirai':
        return [(name, None, chamber) for name, chamber in re.findall(
            r'^([^\n]+)\n\n(衆議院議員|参議院議員)\n', text, re.M)]
    if ident == 'komei':
        return [(name, reading, chamber+'議員') for chamber, name, reading in re.findall(
            r'^- (衆議院|参議院)\n: ([^（\n]+) （([^）]+)）', text, re.M)]
    raise ValueError(ident)


def matching_members(entry, members):
    name, reading, chamber = entry
    candidates = [m for m in members if chamber is None or m['chamber']+'議員' == chamber]
    exact = [m for m in candidates if compact(name) == compact(m['name'])]
    if exact:
        return exact if len(exact) == 1 else []
    # Party sites sometimes combine a kana display name with its reading.
    # Use full readings with a chamber restriction, never surname matching.
    kana = [m for m in candidates if m.get('reading') and (
        compact(reading) == compact(m['reading']) if reading else
        compact(name).endswith(compact(m['reading'])))]
    return kana if len(kana) == 1 else []


def apply_party_rosters(members, root, source, checked_at):
    for m in members:
        m['party_evidence'] = []
        if m['party'] is not None:
            m['party_evidence'].append(dict(party=m['party'], url=m['party_source'],
                as_of=m['party_as_of'], checked_at=checked_at, source_id='tochigi-party',method='自治体の所属党欄'))
    stats = {}
    for ident, (party, url) in PARTIES.items():
        filename=f'party-{ident}.txt'; text=(root/filename).read_text()
        source_id=f'party-{ident}'
        source(source_id, filename, f'{party}：公式国会議員一覧の抽出', url,
               '現職国会議員の欄のみ。資料日未確認、取得日を党籍の基準日に読み替えない。',
               text, kind='所属党')
        records=entries(ident,text); matched=set()
        for entry in records:
            for m in matching_members(entry,members):
                matched.add(m['id'])
                m['party_evidence'].append(dict(party=party,url=url,as_of=None,
                    checked_at=checked_at,source_id=source_id,method='党公式の現職国会議員欄',
                    roster_name=entry[0],roster_reading=entry[1]))
        stats[party]=dict(extracted=len(records),matched=len(matched))
    apply_reviewed_profiles(members, root, source, checked_at)
    apply_party_expansion(members, root, source)
    from verify_party_followup import apply_party_followup
    apply_party_followup(members, root, source)
    from verify_roster_followup import apply_roster_followup
    from verify_party_third_stage import apply_party_third_stage
    apply_roster_followup(members, root, source)
    apply_party_third_stage(members, root, source)
    from verify_roster_continued import apply_roster_continued
    apply_roster_continued(members, root, source)
    for m in members:
        options={e['party'] for e in m['party_evidence']}
        if len(options)>1:
            m.update(party=None,party_source=None,party_as_of=None,party_status='資料間不一致')
        elif options:
            e=m['party_evidence'][0]
            m.update(party=e['party'],party_source=e['url'],party_as_of=e['as_of'],
                     party_checked_at=max(p['checked_at'] for p in m['party_evidence']),party_status='一次資料照合')
        else:
            m.update(party_status='未照合')
    return stats


def reviewed_profiles(root):
    registry=json.loads((root.parent/'party-profile-evidence.json').read_text())
    for s in registry['sources']:
        u=urlsplit(s['url'])
        assert u.scheme=='https' and not u.username and not u.password and u.port in (None,443)
        assert u.hostname in {'www.jimin.jp','www.jimin-aichi.or.jp','sanseito-aichi.com'}
        assert re.fullmatch(r'party-[a-z0-9-]+\.txt',s['filename'])
        assert s['as_of'] is None  # These selected pages do not establish a dated party history.
        assert hashlib.sha256((root/s['filename']).read_bytes()).hexdigest()==s['sha256']
    return registry


def apply_reviewed_profiles(members, root, source, checked_at):
    registry=reviewed_profiles(root)
    sources={s['id']:s for s in registry['sources']}
    people={m['id']:m for m in members}
    for s in sources.values():
        source(s['id'],s['filename'],'所属党：個別プロフィール・県連の現職議員欄',s['url'],
               s['method']+'。資料日未確認、確認日を党籍の基準日に読み替えない。',
               (root/s['filename']).read_text(),kind='所属党')
    for e in registry['entries']:
        s=sources[e['source_id']]
        assert e['party_source']==s['url'] and e['party_as_of'] is None
        m=people.get(e['member_id'])
        if m is None:continue  # Unit tests can pass a subset of the national roster.
        assert m['name']==e['member_name'] and m['chamber']==e['chamber']
        assert m['district']==e['district']
        m['party_evidence'].append(dict(party=e['party'],url=s['url'],as_of=None,
            checked_at=checked_at,source_id=s['id'],method=e['method'],roster_name=e['roster_name']))


def reviewed_expansion(root):
    registry=json.loads((root.parent/'reviewed-party-expansion.json').read_text())
    for s in registry['sources']:
        u=urlsplit(s['url'])
        assert u.scheme=='https' and not u.username and not u.password and u.port in (None,443)
        assert u.hostname in {'www.jimin.jp','cdp-japan.jp'}
        assert re.fullmatch(r'party-[a-z0-9-]+\.txt',s['filename'])
        assert s['as_of'] is None
        assert hashlib.sha256((root/s['filename']).read_bytes()).hexdigest()==s['sha256']
    return registry


def apply_party_expansion(members, root, source):
    registry=reviewed_expansion(root)
    sources={s['id']:s for s in registry['sources']}
    people={m['id']:m for m in members}
    for s in sources.values():
        source(s['id'],s['filename'],s['party']+'：公式全国現職一覧の原本照合',s['url'],
               s['method']+'。資料日未確認。', (root/s['filename']).read_text(),kind='所属党')
    for e in registry['entries']:
        s=sources[e['source_id']]
        assert e['party_source']==s['url'] and e['party_as_of'] is None
        m=people.get(e['member_id'])
        if m is None:continue
        assert (m['name'],m['chamber'],m.get('district'))==(e['member_name'],e['chamber'],e['district'])
        m['party_evidence'].append(dict(party=e['party'],url=s['url'],as_of=None,
            checked_at=e['checked_at'],source_id=s['id'],method=e['method'],roster_name=e['roster_name'],
            original_location=e['original_location'],excerpt_line=e['excerpt_line']))
