"""Join exact LDP names using independently verified parliamentary districts.

A missing baseline district is not inferred from the party. The House original
must first establish it. Existing party evidence, conflicts, names and dates stay
intact. Display-name variants and full-reading-only candidates remain on hold.
"""
import argparse
import hashlib
import json
import re
from urllib.parse import urlsplit
from verify_party_expansion import ldp_entries, compact, district_matches
from verify_roster_followup import ROOT, CACHE, CHECKED_AT, fetch_targets

REPORT = ROOT / 'data/reviewed-party-third-stage.json'
SID = 'party-ldp-third-stage'
URL = 'https://www.jimin.jp/member/data/member.json'
METHOD = '党公式全国現職JSONの正式名・院・選挙区一意一致＋衆議院HTML原本で欠落選挙区を独立補完'


def checked(url):
    p = urlsplit(url)
    assert p.scheme == 'https' and p.hostname == 'www.jimin.jp'
    assert not p.username and not p.password and p.port in (None, 443)
    return url


def verified_member(member, district):
    return dict(member, district=district,
        election_type='比例代表' if district.startswith('（比）') else '小選挙区')


def generate():
    raw = (CACHE / (SID + '.bin')).read_bytes()
    receipt = json.loads((CACHE / (SID + '.receipt.json')).read_text())
    assert receipt['url'] == URL and 0 < len(raw) == receipt['bytes'] < 4_000_000
    assert hashlib.sha256(raw).hexdigest() == receipt['sha256_original']
    records = ldp_entries(raw)
    members = json.loads((ROOT / 'public/data.json').read_text())['legislators']
    roster = json.loads((ROOT / 'data/reviewed-roster-followup.json').read_text())
    official = {e['member_id']: e for e in roster['entries']}
    previous = json.loads(REPORT.read_text()) if REPORT.exists() else {'entries': []}
    prior_ids = {e['member_id'] for e in previous['entries']}
    report = dict(schema_version=1, checked_at=CHECKED_AT,
        note='党の現職JSONと国会の原本を独立確認し、正式名・院・現職選挙区が一意一致する追加分だけを収載。旧抽出で選挙区が欠けた議員の地域を党資料から推定しない。党資料日はnull。会派や取得日から党籍を推定せず、不一致は解消しない。',
        sources=[], originals=[receipt], entries=[], issues=[], stats={})
    filename = SID + '.txt'
    selected = []
    for m in members:
        prior_evidence = [e for e in m['party_evidence'] if e.get('source_id') != SID]
        if prior_evidence and m['id'] not in prior_ids:
            continue
        candidates = [r for r in records if r['chamber'] == m['chamber'] and compact(r['name']) == compact(m['name'])]
        r = official.get(m['id'])
        if len(candidates) != 1 or r is None:
            # Do not treat matching readings as an undocumented name alias.
            variants = [e for e in records if e['chamber'] == m['chamber'] and compact(e['reading']) == compact(m.get('reading'))]
            if variants:
                report['issues'].append(dict(member_id=m['id'], member_name=m['name'], status='保留',
                    roster_names=[e['name'] for e in variants], reason='党の正式氏名完全一致または議会原本照合が未完了。読みだけで同一人物と確定しない。'))
            continue
        entry = candidates[0]
        verified = verified_member(m, r['newverifieddistrict'])
        if not district_matches(verified, entry):
            report['issues'].append(dict(member_id=m['id'], member_name=m['name'], status='保留', reason='党と議会原本の現職選挙区が一致しない'))
            continue
        selected.append(entry)
        report['entries'].append(dict(member_id=m['id'], member_name=m['name'], chamber=m['chamber'],
            district=r['newverifieddistrict'], baseline_district=r['olddistrict'],
            party=entry['party'], party_source=URL, party_as_of=None, checked_at=CHECKED_AT,
            source_id=SID, method=METHOD, roster_name=entry['name'], roster_reading=entry['reading'],
            roster_district=entry['prefecture'] or entry['prs'], profile_url=entry['profile_url'],
            original_location=entry['original_location'], excerpt_line=len(selected),
            parliamentary_roster_evidence=r['evidence'], parliamentary_roster_as_of=r['newverified_as_of']))
    text = '\n'.join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in selected) + '\n'
    (ROOT / 'data/source-text' / filename).write_text(text)
    report['sources'].append(dict(id=SID, url=URL, filename=filename, party='自由民主党', as_of=None,
        checked_at=CHECKED_AT, sha256=hashlib.sha256(text.encode()).hexdigest(), original=receipt, method=METHOD,
        supporting_source_ids=sorted({e['parliamentary_roster_evidence']['source_id'] for e in report['entries']})))
    report['stats'] = dict(additional_exact_name_matches=len(report['entries']), held_name_or_roster_variants=len(report['issues']))
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    validate()


def validate():
    report = json.loads(REPORT.read_text()); source = report['sources'][0]
    checked(source['url']); assert source['url'] == URL and source['as_of'] is None
    assert source['id'] == SID and source['original'] == report['originals'][0]
    original = source['original']; checked(original['url']); checked(original['final_url'])
    assert original['download_status'] == 'downloaded' and original['url'] == URL
    assert re.fullmatch(r'[0-9a-f]{64}', original['sha256_original']) and 0 < original['bytes'] < 4_000_000
    assert source['filename'] == SID + '.txt'
    raw = (ROOT / 'data/source-text' / source['filename']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == source['sha256']
    records = [json.loads(line) for line in raw.decode().splitlines()]
    roster = json.loads((ROOT / 'data/reviewed-roster-followup.json').read_text())
    official = {e['member_id']: e for e in roster['entries']}
    members = {m['id']: m for m in json.loads((ROOT / 'public/data.json').read_text())['legislators']}
    seen = set()
    for e in report['entries']:
        m = members[e['member_id']]; r = official[m['id']]
        assert m['id'] not in seen; seen.add(m['id'])
        entry = records[e['excerpt_line'] - 1]
        assert set(entry) == {'party', 'name', 'reading', 'chamber', 'prefecture', 'prs', 'profile_url', 'original_location', 'status_label'}
        assert compact(entry['name']) == compact(m['name']) and entry['chamber'] == m['chamber']
        assert (m['name'], m['chamber']) == (e['member_name'], e['chamber'])
        assert r['olddistrict'] == e['baseline_district'] and r['newverifieddistrict'] == e['district']
        assert m.get('district') in {e['baseline_district'], e['district']}
        assert e['parliamentary_roster_evidence'] == r['evidence'] and e['parliamentary_roster_as_of'] == r['newverified_as_of']
        assert district_matches(verified_member(m, e['district']), entry)
        assert e['party'] == entry['party'] == source['party'] and e['party_as_of'] is None and e['party_source'] == URL
        checked(entry['profile_url']); assert entry['original_location'] == e['original_location']
    assert report['stats']['additional_exact_name_matches'] == len(seen)
    print(json.dumps(report['stats'], ensure_ascii=False))
    return report


def apply_party_third_stage(members, root, source):
    report = json.loads((root.parent / 'reviewed-party-third-stage.json').read_text())
    s = report['sources'][0]
    assert s['url'] == URL and s['as_of'] is None
    assert s['filename'] == SID + '.txt'
    assert hashlib.sha256((root / s['filename']).read_bytes()).hexdigest() == s['sha256']
    source(s['id'], s['filename'], '自由民主党：議会原本による選挙区補完後の党籍照合', s['url'],
        METHOD + '。党の資料日は未確認。議会原本の資料日を党籍の日付にしない。', (root / s['filename']).read_text(), kind='所属党')
    official = {e['member_id']: e for e in json.loads((root.parent / 'reviewed-roster-followup.json').read_text())['entries']}
    people = {m['id']: m for m in members}
    records = [json.loads(line) for line in (root / s['filename']).read_text().splitlines()]
    for e in report['entries']:
        m = people.get(e['member_id'])
        if m is None:
            continue
        r = official[m['id']]; entry = records[e['excerpt_line'] - 1]
        assert (m['name'], m['chamber']) == (e['member_name'], e['chamber'])
        assert m.get('district') in {e['baseline_district'], e['district']}
        assert r['newverifieddistrict'] == e['district'] and r['evidence'] == e['parliamentary_roster_evidence']
        assert compact(entry['name']) == compact(m['name']) and district_matches(verified_member(m, e['district']), entry)
        assert entry['chamber'] == m['chamber'] and entry['status_label'] == m['chamber'] + '議員'
        assert entry['party'] == e['party'] == s['party'] == '自由民主党'
        assert e['party_source'] == URL and e['party_as_of'] is None
        m['party_evidence'].append(dict(party=e['party'], url=URL, as_of=None, checked_at=e['checked_at'], source_id=SID,
            method=METHOD, roster_name=e['roster_name'], roster_reading=e['roster_reading'],
            original_location=e['original_location'], excerpt_line=e['excerpt_line'],
            parliamentary_roster_evidence=e['parliamentary_roster_evidence'], parliamentary_roster_as_of=e['parliamentary_roster_as_of']))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--fetch', action='store_true'); parser.add_argument('--generate', action='store_true'); args = parser.parse_args()
    if args.fetch:
        fetch_targets({SID: URL}, checked)
    if args.generate:
        generate()
    elif not args.fetch:
        validate()
