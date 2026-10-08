"""Join reviewed party rosters; a caucus label is never a party assignment."""
import re
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
    for m in members:
        options={e['party'] for e in m['party_evidence']}
        if len(options)>1:
            m.update(party=None,party_source=None,party_as_of=None,party_status='資料間不一致')
        elif options:
            e=m['party_evidence'][0]
            m.update(party=e['party'],party_source=e['url'],party_as_of=e['as_of'],
                     party_checked_at=checked_at,party_status='一次資料照合')
        else:
            m.update(party_status='未照合')
    return stats
