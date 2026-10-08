"""Verify the official House HTML rosters without changing baseline identity.

The baseline IDs, names, dates, source URLs and party evidence remain immutable.
Only missing districts are supplied as separate updates, dated to each original
page. A parliamentary caucus is never treated as a party membership statement.
"""
import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urljoin
from urllib.request import build_opener, HTTPRedirectHandler
from verify_party_expansion import DOM, Node, compact
from fetch_sources import failure_details

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/roster-followup'
REPORT = ROOT / 'data/reviewed-roster-followup.json'
TARGETS = {f'house-roster-{i}': f'https://www.shugiin.go.jp/Internet/itdb_annai.nsf/html/statics/syu/{i}giin.htm' for i in range(1, 11)}
CHECKED_AT = '2026-10-09'


def checked(url):
    p = urlsplit(url)
    assert p.scheme == 'https' and p.hostname == 'www.shugiin.go.jp'
    assert not p.username and not p.password and p.port in (None, 443)
    return url


def fetch_targets(targets, checker=checked):
    policy = json.loads(Path('/etc/codex/network-policy.json').read_text())
    allowed = {r['host'] for r in policy['http_network_policy']['egress_rules']}
    assert {urlsplit(u).hostname for u in targets.values()} <= allowed
    class Redirect(HTTPRedirectHandler):
        def redirect_request(self, req, response, code, message, headers, url):
            return super().redirect_request(req, response, code, message, headers, checker(url))
    CACHE.mkdir(parents=True, exist_ok=True)
    failed = 0
    for key, url in targets.items():
        assert re.fullmatch(r'[a-z0-9-]+', key)
        try:
            with build_opener(Redirect()).open(checker(url), timeout=30) as response:
                raw = response.read(4_000_000); assert 0 < len(raw) < 4_000_000
                receipt = dict(source_id=key, url=url, final_url=checker(response.geturl()), bytes=len(raw),
                    sha256_original=hashlib.sha256(raw).hexdigest(), retrieved_at_utc=datetime.now(timezone.utc).isoformat(),
                    content_type=response.headers.get_content_type(), download_status='downloaded')
            (CACHE / (key + '.bin')).write_bytes(raw)
            (CACHE / (key + '.receipt.json')).write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
            print(json.dumps(dict(source_id=key, download_status='downloaded')))
        except Exception as error:
            failed += 1
            print(json.dumps(dict(source_id=key, download_status='blocked_or_failed', **failure_details(error))))
    return failed


def original(key):
    raw = (CACHE / (key + '.bin')).read_bytes()
    receipt = json.loads((CACHE / (key + '.receipt.json')).read_text())
    assert 0 < len(raw) == receipt['bytes'] < 4_000_000
    assert hashlib.sha256(raw).hexdigest() == receipt['sha256_original']
    checked(receipt['url']); checked(receipt['final_url']); assert receipt['url'] == TARGETS[key]
    return raw, receipt


def descendants(node, tag):
    return [node] if node.tag == tag else [y for x in node.children if isinstance(x, Node) for y in descendants(x, tag)]


def parse_original(raw):
    dom = DOM(); dom.feed(raw.decode('cp932'))
    date = re.search(r'令和\s*8\s*年\s*(\d+)\s*月\s*(\d+)\s*日\s*現在', dom.root.text())
    assert date
    as_of = f'2026-{int(date[1]):02d}-{int(date[2]):02d}'
    records = []
    for index, row in enumerate(descendants(dom.root, 'tr')):
        cells = [n for n in row.children if isinstance(n, Node) and n.tag == 'td']
        if len(cells) != 5 or not cells[0].text().strip().endswith('君'):
            continue
        name = compact(cells[0].text().strip().removesuffix('君'))
        reading = compact(cells[1].text())
        district = compact(cells[3].text()).replace('(比)', '（比）')
        assert re.fullmatch(r'(?:（比）.+|[^\d ]+\d+)', district)
        link = descendants(cells[0], 'a')
        assert len(link) == 1
        profile_url = checked(urljoin('https://www.shugiin.go.jp/Internet/itdb_annai.nsf/html/statics/syu/1giin.htm', link[0].attrs['href']))
        records.append(dict(name=name, reading=reading, chamber='衆議院', district=district,
            caucus=compact(cells[2].text()), as_of=as_of, profile_url=profile_url,
            original_location=f'HTML table tr[{index}] cells[0:5] 氏名/ふりがな/会派/選挙区/当選回数'))
    return records


def generate():
    members = json.loads((ROOT / 'public/data.json').read_text())['legislators']
    report = dict(schema_version=1, checked_at=CHECKED_AT,
        note='衆議院公式原本の表セルを独立抽出して照合。旧名簿の氏名・ID・資料日・URL・党籍根拠は変更しない。欠落選挙区だけを別の確認記録で補完。会派から党名を推定せず、配分決定時点の議員の確認とは別。',
        sources=[], originals=[], entries=[], roster_updates=[], issues=[], stats={})
    seen = set()
    # Retain original null fields when rerun after the root has applied updates.
    previous = json.loads(REPORT.read_text()) if REPORT.exists() else {'roster_updates': []}
    baseline_updates = {r['member_id']: r for r in previous['roster_updates']}
    for key, url in TARGETS.items():
        raw, receipt = original(key)
        records = parse_original(raw)
        sid = 'roster-' + key + '-original'
        filename = sid + '.txt'
        text = '\n'.join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in records) + '\n'
        (ROOT / 'data/source-text' / filename).write_text(text)
        assert records
        report['sources'].append(dict(id=sid, url=url, filename=filename, as_of=records[0]['as_of'],
            checked_at=CHECKED_AT, sha256=hashlib.sha256(text.encode()).hexdigest(), original=receipt,
            title='衆議院議員一覧：公式HTML原本 ' + key.rsplit('-', 1)[-1] + '頁',
            method='公式表の正式氏名と院の一意一致、読み・選挙区・会派を各セルから独立抽出'))
        report['originals'].append(receipt)
        for line, r in enumerate(records, 1):
            candidates = [m for m in members if m['chamber'] == '衆議院' and compact(m['name']) == compact(r['name'])]
            if len(candidates) != 1:
                report['issues'].append(dict(source_id=sid, roster_name=r['name'], status='保留', reason='正式氏名・院で一意一致しない'))
                continue
            m = candidates[0]
            assert m['id'] not in seen; seen.add(m['id'])
            base = baseline_updates.get(m['id'])
            olddistrict = base['olddistrict'] if base else m.get('district')
            baseline_as_of = base['baseline_as_of'] if base else m['as_of']
            baseline_source_url = base['baseline_source_url'] if base else m['source_url']
            evidence = dict(source_id=sid, excerpt_line=line, original_location=r['original_location'])
            entry = dict(member_id=m['id'], member_name=m['name'], chamber=m['chamber'], olddistrict=olddistrict,
                newverifieddistrict=r['district'], newverifiedreading=r['reading'], newverifiedcaucus=r['caucus'],
                newverified_as_of=r['as_of'], baseline_as_of=baseline_as_of, baseline_source_url=baseline_source_url,
                profile_url=r['profile_url'], evidence=evidence)
            report['entries'].append(entry)
            if olddistrict is None:
                report['roster_updates'].append(entry)
            elif olddistrict != r['district']:
                report['issues'].append(dict(source_id=sid, member_id=m['id'], roster_name=r['name'], status='資料間不一致',
                    olddistrict=olddistrict, official_district=r['district'], reason='既存選挙区と原本が一致しないため上書きしない'))
    missing = [m['id'] for m in members if m['chamber'] == '衆議院' and m['id'] not in seen]
    report['stats'] = dict(official_house_records=sum(len((ROOT / 'data/source-text' / s['filename']).read_text().splitlines()) for s in report['sources']),
        matched=len(seen), missing_district_updates=len(report['roster_updates']), unmatched_baseline_ids=missing,
        original_dates=sorted({s['as_of'] for s in report['sources']}))
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    validate()


def validate():
    report = json.loads(REPORT.read_text()); sources = {s['id']: s for s in report['sources']}
    members = {m['id']: m for m in json.loads((ROOT / 'public/data.json').read_text())['legislators']}
    excerpts = {}
    originals = {o['source_id']: o for o in report['originals']}
    assert set(originals) == set(TARGETS)
    for sid, s in sources.items():
        checked(s['url']); assert s['url'] in TARGETS.values()
        assert s['original'] == originals[s['original']['source_id']]
        assert re.fullmatch(r'roster-house-roster-\d+-original\.txt', s['filename'])
        raw = (ROOT / 'data/source-text' / s['filename']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == s['sha256']
        excerpts[sid] = [json.loads(line) for line in raw.decode().splitlines()]
        for e in excerpts[sid]:
            assert set(e) == {'name', 'reading', 'chamber', 'district', 'caucus', 'as_of', 'profile_url', 'original_location'}
            checked(e['profile_url']); assert e['as_of'] == s['as_of']
    seen = set()
    for e in report['entries']:
        m = members[e['member_id']]; assert m['id'] not in seen; seen.add(m['id'])
        assert (m['name'], m['chamber'], m['as_of'], m['source_url']) == (e['member_name'], e['chamber'], e['baseline_as_of'], e['baseline_source_url'])
        assert m.get('district') in {e['olddistrict'], e['newverifieddistrict']}
        evidence = e['evidence']; row = excerpts[evidence['source_id']][evidence['excerpt_line'] - 1]
        assert compact(row['name']) == compact(m['name']) and row['chamber'] == m['chamber']
        assert row['district'] == e['newverifieddistrict'] and row['reading'] == e['newverifiedreading']
        assert row['caucus'] == e['newverifiedcaucus'] and row['as_of'] == e['newverified_as_of']
        assert row['original_location'] == evidence['original_location'] and row['profile_url'] == e['profile_url']
    for e in report['roster_updates']:
        assert e in report['entries'] and e['olddistrict'] is None
    assert report['stats']['matched'] == len(seen) and report['stats']['missing_district_updates'] == len(report['roster_updates'])
    print(json.dumps(report['stats'], ensure_ascii=False))


def apply_roster_followup(members, root, source):
    report = json.loads((root.parent / 'reviewed-roster-followup.json').read_text())
    sources = {s['id']: s for s in report['sources']}
    excerpts = {}
    for s in sources.values():
        checked(s['url']); assert s['url'] in TARGETS.values()
        assert re.fullmatch(r'roster-house-roster-\d+-original\.txt', s['filename'])
        raw = (root / s['filename']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == s['sha256']
        excerpts[s['id']] = [json.loads(line) for line in raw.decode().splitlines()]
        source(s['id'], s['filename'], s['title'], s['url'],
            s['method'] + '。会派は党籍と区別。旧資料日は上書きしない。', raw.decode(), s['as_of'], kind='議員名簿')
    people = {m['id']: m for m in members}
    updated = {e['member_id'] for e in report['roster_updates']}
    prefectures = json.loads((ROOT / 'public/data.json').read_text())['prefectures']
    short = {p if p == '北海道' else p[:-1]: p for p in prefectures}
    for e in report['entries']:
        m = people.get(e['member_id'])
        if m is None:
            continue
        assert (m['name'], m['chamber'], m['as_of'], m['source_url']) == (e['member_name'], e['chamber'], e['baseline_as_of'], e['baseline_source_url'])
        assert m.get('district') in {e['olddistrict'], e['newverifieddistrict']}
        evidence = e['evidence']; row = excerpts[evidence['source_id']][evidence['excerpt_line'] - 1]
        assert compact(row['name']) == compact(m['name']) and row['chamber'] == m['chamber']
        assert row['district'] == e['newverifieddistrict'] and row['as_of'] == e['newverified_as_of']
        assert row['reading'] == e['newverifiedreading'] and row['caucus'] == e['newverifiedcaucus']
        if 'baseline_roster' not in m:
            m['baseline_roster'] = {key: m.get(key) for key in ['district', 'as_of', 'source_url', 'reading', 'caucus', 'election_type']}
            m['baseline_roster']['prefectures'] = list(m.get('prefectures', []))
        assert m['baseline_roster']['district'] == e['olddistrict']
        m.setdefault('roster_evidence', []).append(dict(source_id=evidence['source_id'],
            url=sources[evidence['source_id']]['url'], as_of=e['newverified_as_of'], checked_at=report['checked_at'],
            district=e['newverifieddistrict'], reading=e['newverifiedreading'], caucus=e['newverifiedcaucus'],
            profile_url=e['profile_url'], original_location=evidence['original_location'], excerpt_line=evidence['excerpt_line'],
            district_completed=e['member_id'] in updated,
            method='正式名・院で一意一致した衆議院原本の表セルを独立照合。会派から党籍を推定しない。'))
        if m['id'] in updated:
            assert e['olddistrict'] is None
            district = e['newverifieddistrict']
            proportional = district.startswith('（比）')
            province = re.sub(r'\d+$', '', district)
            assert proportional or province in short
            m.update(district=district, election_type='比例代表' if proportional else '小選挙区',
                prefectures=[] if proportional else [short[province]])
    return report['stats']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--fetch', action='store_true'); parser.add_argument('--generate', action='store_true'); args = parser.parse_args()
    if args.fetch:
        fetch_targets(TARGETS)
    if args.generate:
        generate()
    elif not args.fetch:
        validate()
