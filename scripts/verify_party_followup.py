"""Verify further official party originals; never infer a party from caucuses.

Raw originals stay in ignored .cache/party-followup. --generate extracts only
name, chamber, elected district and original location. Undated pages do not
establish party history or membership when a budget allocation was decided.
--fetch refreshes only reviewed official URLs, preserving proxy and TLS.
Run --generate separately after inspecting changed originals.
"""
import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.request import build_opener, HTTPRedirectHandler
from fetch_sources import failure_details
from pathlib import Path
from urllib.parse import urlsplit
from verify_party_expansion import DOM, Node, compact, geographical

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/party-followup'
REPORT = ROOT / 'data/reviewed-party-followup.json'
CHECKED_AT = '2026-10-09'
HOSTS = {'sanseito.jp', 'www.komei.or.jp'}
METHOD = '党公式現職欄の正式名・院・当選した選挙区の一意一致'


def checked(url):
    p = urlsplit(url)
    assert p.scheme == 'https' and p.hostname in HOSTS
    assert not p.username and not p.password and p.port in (None, 443)
    return url


def original(key):
    receipt = json.loads((CACHE / (key + '.receipt.json')).read_text())
    raw = (CACHE / (key + '.bin')).read_bytes()
    assert len(raw) == receipt['bytes'] < 4_000_000
    assert hashlib.sha256(raw).hexdigest() == receipt['sha256_original']
    checked(receipt['url']); checked(receipt['final_url'])
    return raw, receipt


def fetch_reviewed_originals(report_path, checker):
    """Refresh only committed reviewed URLs; failures never stop other fetches.

    The report is unchanged. Cache receipts describe this fetch separately from
    published evidence. Use --generate only after inspecting changed originals.
    """
    report = json.loads(report_path.read_text())
    policy = json.loads(Path('/etc/codex/network-policy.json').read_text())
    allowed = {r['host'] for r in policy['http_network_policy']['egress_rules']}
    reviewed = report['originals']
    assert {urlsplit(o['url']).hostname for o in reviewed} <= allowed
    class Redirect(HTTPRedirectHandler):
        def redirect_request(self, req, response, code, message, headers, url):
            return super().redirect_request(req, response, code, message, headers, checker(url))
    CACHE.mkdir(parents=True, exist_ok=True)
    failed = 0
    for item in reviewed:
        key = item['source_id']
        assert re.fullmatch(r'[a-z0-9-]+', key)
        url = checker(item['url'])
        try:
            with build_opener(Redirect()).open(url, timeout=30) as response:
                raw = response.read(4_000_000)
                assert 0 < len(raw) < 4_000_000
                receipt = dict(source_id=key, url=url, final_url=checker(response.geturl()),
                    retrieved_at_utc=datetime.now(timezone.utc).isoformat(),
                    sha256_original=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
                    content_type=response.headers.get_content_type(), download_status='downloaded')
            (CACHE / (key + '.bin')).write_bytes(raw)
            (CACHE / (key + '.receipt.json')).write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
            print(json.dumps(dict(source_id=key, download_status='downloaded',
                same_as_reviewed=receipt['sha256_original'] == item['sha256_original'])))
        except Exception as error:
            failed += 1
            # Do not expose exception strings, credentials, headers or contacts.
            print(json.dumps(dict(source_id=key, download_status='blocked_or_failed', **failure_details(error))))
    return failed


def sanseito_records(raw, key):
    data = json.loads(raw)
    records = data['candidate_posts'] if isinstance(data, dict) else data
    selected = []
    for index, r in enumerate(records):
        chamber = r.get('選挙区分', '').replace('選挙', '')
        if chamber not in {'衆議院議員', '参議院議員'}:
            continue
        # Campaign districts can differ from the elected proportional district.
        # The explicit current chamber field is the only accepted district here.
        elected = r.get('所属議会', '')
        match = re.fullmatch(r'(衆議院|参議院)議員[（(]([^）)]+)[）)]', elected)
        assert match and match[1] + '議員' == chamber
        district = compact(match[2]).removeprefix('比例').removesuffix('ブロック').removesuffix('選挙区')
        if match[1] == '参議院' and district == '全国':
            district = '全国比例'
        selected.append(dict(party='参政党', name=r['名前大'], chamber=match[1],
            elected_district=district, current_chamber_field=elected,
            original_key=key, original_location=f'JSON {"candidate_posts" if isinstance(data, dict) else "array"}[{index}] id={r["id"]} 所属議会/選挙区分/名前大'))
    return selected


def komei_records(raw):
    parser = DOM(); parser.feed(raw.decode('utf-8'))
    selected = []
    cards = parser.root.all('indexCol3_02')
    assert len(cards) == 1
    for index, card in enumerate(cards[0].children):
        if not isinstance(card, Node):
            continue
        def find(node, tag):
            return [node] if node.tag == tag else [y for x in node.children if isinstance(x, Node) for y in find(x, tag)]
        chamber = find(card, 'dt')[0].text().strip()
        assert chamber in {'衆議院', '参議院'}
        name = find(card, 'dd')[0].text().split('（')[0].strip()
        link = checked('https://www.komei.or.jp' + find(card, 'a')[0].attrs['href'])
        key = 'komei-detail-' + link.rsplit('/', 1)[-1]
        if not (CACHE / (key + '.bin')).exists():
            selected.append(dict(party='公明党', name=name, chamber=chamber, elected_district=None,
                profile_url=link, original_key=None, original_location=f'HTML .indexCol3_02 card[{index}]',
                issue='議会名簿の正式名・院一意一致またはプロフィール取得が未完了'))
            continue
        profile, _ = original(key)
        dom = DOM(); dom.feed(profile.decode('utf-8'))
        heading = dom.root.all('hdg1_01')[0].text().split('（')[0].strip()
        label = dom.root.all('hdg2_01')[0].text().strip()
        assert compact(heading) == compact(name) and label == chamber + '議員'
        fields = {}
        for item in find(dom.root, 'dl'):
            dt, dd = find(item, 'dt'), find(item, 'dd')
            if dt and dd:
                fields[dt[0].text().strip()] = dd[0].text().strip()
        district = fields['選挙区']
        elected = compact(district.split('/')[0]).removesuffix('B')
        if chamber == '参議院' and elected == '比例区':
            elected = '全国比例'
        selected.append(dict(party='公明党', name=name, chamber=chamber, elected_district=elected,
            district_field=district, profile_url=link, original_key=key,
            original_location=f'HTML .indexCol3_02 card[{index}]; profile h1/h2/dl 選挙区'))
    return selected


def matches(member, entry):
    if not member.get('district') or not entry.get('elected_district'):
        return False
    expected = compact(member['district']).removeprefix('(比)')
    elected = entry['elected_district']
    if member['election_type'] == '比例代表':
        return expected == elected
    return geographical(expected) == geographical(elected)


def generate():
    originals = {}
    def get(key):
        raw, receipt = original(key); originals[key] = receipt; return raw
    member_page = get('sanseito-member').decode('utf-8')
    logic = get('sanseito-logic').decode('utf-8')
    assert '参政党の議員' in member_page and '選挙区分' in logic
    assert 'member.選挙区分 == hero_head[0].title' in logic
    report = dict(schema_version=1, checked_at=CHECKED_AT,
        note='現職欄・正式氏名・院・当選選挙区を照合。党籍資料日は不明。候補選挙区・会派から推定しない。予算配分決定時点の党籍を示さない。異なる党の既存根拠を消さない。',
        sources=[], originals=[], entries=[], issues=[], stats={})
    members = json.loads((ROOT / 'public/data.json').read_text())['legislators']
    datasets = []
    for key in ['sanseito-primary', 'sanseito-secondary']:
        raw = get(key)
        assert originals[key]['url'].split('https://sanseito.jp')[1] in member_page
        datasets.append(('party-' + key + '-followup', key, sanseito_records(raw, key), ['sanseito-member', 'sanseito-logic']))
    raw = get('komei-national')
    assert '49人' in raw.decode('utf-8')
    records = komei_records(raw)
    for entry in records:
        if entry.get('original_key'):
            get(entry['original_key'])
    datasets.append(('party-komei-national-followup', 'komei-national', records,
        [e['original_key'] for e in records if e.get('original_key')]))
    for sid, key, records, supporting in datasets:
        selected = '\n'.join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in records) + '\n'
        filename = sid + '.txt'
        (ROOT / 'data/source-text' / filename).write_text(selected)
        report['sources'].append(dict(id=sid, url=originals[key]['url'], filename=filename,
            party=records[0]['party'], as_of=None, checked_at=CHECKED_AT,
            sha256=hashlib.sha256(selected.encode()).hexdigest(), method=METHOD,
            original=originals[key], supporting_original_ids=supporting))
        count = new = conflicts = 0
        for line, e in enumerate(records, 1):
            candidates = [m for m in members if compact(m['name']) == compact(e['name']) and m['chamber'] == e['chamber']]
            if len(candidates) != 1 or not matches(candidates[0], e):
                report['issues'].append(dict(source_id=sid, roster_name=e['name'], roster_chamber=e['chamber'],
                    status='保留', reason='正式名・院の一意一致または当選選挙区一致を満たさない',
                    roster_district=e.get('elected_district')))
                continue
            m = candidates[0]
            report['entries'].append(dict(member_id=m['id'], member_name=m['name'], chamber=m['chamber'],
                district=m['district'], party=e['party'], party_source=originals[key]['url'], party_as_of=None,
                checked_at=CHECKED_AT, source_id=sid, method=METHOD, roster_name=e['name'],
                roster_district=e['elected_district'], original_location=e['original_location'], excerpt_line=line,
                supporting_original_id=e.get('original_key'), profile_url=e.get('profile_url')))
            prior = [p for p in m['party_evidence'] if p.get('source_id') not in {'party-sanseito-primary-followup', 'party-sanseito-secondary-followup', 'party-komei-national-followup'}]
            count += 1; new += not prior
            conflicts += any(p['party'] != e['party'] for p in prior)
        report['stats'][sid] = dict(extracted=len(records), matched=count,
            previously_unverified=new, existing_other_party_evidence=conflicts)
    report['originals'] = list(originals.values())
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    validate()


def registry(root):
    report = json.loads((root.parent / 'reviewed-party-followup.json').read_text())
    originals = {o['source_id']: o for o in report['originals']}
    for o in originals.values():
        checked(o['url']); checked(o['final_url'])
        assert o['download_status'] == 'downloaded' and re.fullmatch(r'[a-f0-9]{64}', o['sha256_original'])
        assert 0 < o['bytes'] < 4_000_000
    for s in report['sources']:
        checked(s['url']); assert s['as_of'] is None
        assert re.fullmatch(r'party-[a-z0-9-]+\.txt', s['filename'])
        assert hashlib.sha256((root / s['filename']).read_bytes()).hexdigest() == s['sha256']
        assert s['original'] == originals[s['original']['source_id']]
        assert s['url'] == s['original']['url'] and s['checked_at'] == report['checked_at']
        records = [json.loads(line) for line in (root / s['filename']).read_text().splitlines()]
        for entry in records:
            assert set(entry) <= {'party', 'name', 'chamber', 'elected_district', 'current_chamber_field', 'original_key', 'original_location', 'profile_url', 'district_field', 'issue'}
            assert entry['party'] == s['party'] and entry['chamber'] in {'衆議院', '参議院'}
            if entry.get('profile_url'):
                checked(entry['profile_url'])
            if entry.get('original_key'):
                assert entry['original_key'] in originals
        assert set(s['supporting_original_ids']) <= originals.keys()
    return report


def apply_party_followup(members, root, source):
    report = registry(root)
    sources = {s['id']: s for s in report['sources']}
    people = {m['id']: m for m in members}
    for s in sources.values():
        source(s['id'], s['filename'], s['party'] + '：現職欄・当選選挙区の原本照合', s['url'],
            s['method'] + '。資料日未確認。候補選挙区は使用しない。', (root / s['filename']).read_text(), kind='所属党')
    for e in report['entries']:
        s = sources[e['source_id']]
        assert e['party_source'] == s['url'] and e['party_as_of'] is None
        m = people.get(e['member_id'])
        if m is None:
            continue
        assert (m['name'], m['chamber'], m.get('district')) == (e['member_name'], e['chamber'], e['district'])
        m['party_evidence'].append(dict(party=e['party'], url=s['url'], as_of=None, checked_at=e['checked_at'],
            source_id=s['id'], method=e['method'], roster_name=e['roster_name'], roster_district=e['roster_district'],
            original_location=e['original_location'], excerpt_line=e['excerpt_line'],
            supporting_original_id=e.get('supporting_original_id'), profile_url=e.get('profile_url')))


def validate():
    report = registry(ROOT / 'data/source-text')
    sources = {s['id']: s for s in report['sources']}
    people = {m['id']: m for m in json.loads((ROOT / 'public/data.json').read_text())['legislators']}
    seen = set()
    for e in report['entries']:
        assert (e['member_id'], e['source_id']) not in seen
        seen.add((e['member_id'], e['source_id']))
        m = people[e['member_id']]; s = sources[e['source_id']]
        assert (m['name'], m['chamber'], m.get('district')) == (e['member_name'], e['chamber'], e['district'])
        records = [json.loads(line) for line in (ROOT / 'data/source-text' / s['filename']).read_text().splitlines()]
        entry = records[e['excerpt_line'] - 1]
        assert compact(m['name']) == compact(entry['name']) and m['chamber'] == entry['chamber'] and matches(m, entry)
        assert entry['original_location'] == e['original_location']
        assert e['party'] == s['party'] == entry['party'] and e['party_source'] == s['url']
        assert e['party_as_of'] is None and e['checked_at'] == report['checked_at']
        assert e['supporting_original_id'] == entry['original_key']
        assert e['roster_district'] == entry['elected_district'] and e['profile_url'] == entry.get('profile_url')
    print(json.dumps(report['stats'], ensure_ascii=False))


if __name__ == '__main__':
    args = argparse.ArgumentParser(); args.add_argument('--fetch', action='store_true'); args.add_argument('--generate', action='store_true'); flags = args.parse_args()
    if flags.fetch:
        fetch_reviewed_originals(REPORT, checked)
    if flags.generate:
        generate()
    elif not flags.fetch:
        validate()
