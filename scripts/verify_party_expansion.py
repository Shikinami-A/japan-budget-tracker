"""Review official LDP/CDP national rosters without inferring parties from caucuses.

--fetch downloads only the fixed official URLs over the injected proxy with TLS.
Raw HTML/JSON/JS remain in ignored .cache. Committed excerpts contain only the
public representative name, reading, chamber, district and official profile URL.
The listed party is undated and cannot establish party membership at a budget
allocation date. Exact full name + chamber + electoral district are required.
"""
import argparse
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urljoin
from urllib.request import build_opener, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/party-expansion'
REPORT = ROOT / 'data/reviewed-party-expansion.json'
HOSTS = {'www.jimin.jp', 'cdp-japan.jp'}
MAX_BYTES = 2 * 1024 * 1024
TARGETS = {
    'party-ldp-expansion': 'https://www.jimin.jp/member/data/member.json',
    'party-ldp-expansion-search': 'https://www.jimin.jp/member/search/',
    'party-ldp-expansion-logic': 'https://www.jimin.jp/assets/js/ui-member.js',
    **{f'party-cdp-expansion-{i}': 'https://cdp-japan.jp/members/all' + (f'?page={i}' if i > 1 else '') for i in range(1, 5)},
}


def checked(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('URL is outside the reviewed official host allowlist')
    return url


class Redirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        return super().redirect_request(request, response, code, message, headers, checked(url))


def compact(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value or ''))


def geographical(value):
    # Remove only official prefecture/constituency suffix spellings, never names.
    return re.sub(r'県|府|都|第|区', '', compact(value))


def district_matches(member, entry):
    if not member.get('district'):
        return False
    if member['election_type'] == '比例代表':
        if member['chamber'] == '参議院':
            return member['district'] == '全国比例' and entry['prs'] in {'比例代表', '比例'}
        return compact(member['district']).removeprefix('(比)') == compact(entry['prs']).removesuffix('ブロック')
    if geographical(member['district']) == geographical(entry['prefecture']):
        return True
    # LDP's own roster rendering JS expands these statutory combined districts.
    return entry['party'] == '自由民主党' and member['district'] in {'徳島県・高知県', '鳥取県・島根県'} and entry['prefecture'] in member['district'].split('・')


class Node:
    def __init__(self, tag, attrs):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def text(self):
        return ''.join(x if isinstance(x, str) else x.text() for x in self.children)

    def all(self, token):
        result = [self] if token in self.attrs.get('class', '').split() else []
        for item in self.children:
            if isinstance(item, Node):
                result.extend(item.all(token))
        return result


class DOM(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__()
        self.root = Node('root', {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        item = Node(tag, attrs)
        self.stack[-1].children.append(item)
        if tag not in self.VOID:
            self.stack.append(item)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def ldp_entries(raw):
    result = []
    for index, item in enumerate(json.loads(raw)):
        if item['parliament'] not in {'衆議院議員', '参議院議員'}:
            continue
        profile = checked(urljoin('https://www.jimin.jp/', item['url']))
        result.append(dict(party='自由民主党', name=item['lastName'] + item['firstName'],
            reading=item['lastNameKana'] + item['firstNameKana'], chamber=item['parliament'].removesuffix('議員'),
            prefecture=item['prefecture'], prs=item['prs'], profile_url=profile,
            original_location=f'JSON array[{index}] id={item["id"]}',
            status_label=item['parliament']))
    return result


def cdp_entries(raw):
    parser = DOM()
    parser.feed(raw.decode('utf-8'))
    result = []
    for index, item in enumerate(parser.root.all('member-search-results')):
        chamber = item.all('house-type')[0].text().strip()
        if chamber not in {'衆議院議員', '参議院議員'}:
            continue
        heading = item.all('member-name')[0]
        name = ''.join(x for x in heading.children if isinstance(x, str)).strip()
        district = item.all('district')[0].text().strip()
        card = item.children[0]
        result.append(dict(party='立憲民主党', name=name,
            reading=heading.children[-1].text() if isinstance(heading.children[-1], Node) else '',
            chamber=chamber.removesuffix('議員'), prefecture=district, prs=district if district == '比例' else '',
            profile_url=checked(urljoin('https://cdp-japan.jp/', card.attrs['href'])),
            original_location=f'HTML .member-search-results[{index}] .member-name/.house-type/.district', status_label=chamber))
    return result


def fetch():
    CACHE.mkdir(parents=True, exist_ok=True)
    receipts = {}
    policy = json.loads(Path('/etc/codex/network-policy.json').read_text())
    allowed = {x['host'] for x in policy['http_network_policy']['egress_rules']}
    assert HOSTS <= allowed, 'Reviewed hosts must be present in the session network policy'
    for sid, url in TARGETS.items():
        with build_opener(Redirect()).open(checked(url), timeout=30) as response:
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError('Official original exceeds the reviewed size limit')
            receipts[sid] = dict(source_id=sid, url=url, final_url=checked(response.geturl()),
                retrieved_at_utc=datetime.now(timezone.utc).isoformat(), sha256_original=hashlib.sha256(raw).hexdigest(),
                bytes=len(raw), content_type=response.headers.get_content_type(), download_status='downloaded')
        (CACHE / (sid + '.bin')).write_bytes(raw)
    (CACHE / 'receipts.json').write_text(json.dumps(receipts, ensure_ascii=False, indent=2) + '\n')


def generate():
    receipts = json.loads((CACHE / 'receipts.json').read_text())
    originals = {}
    for sid in TARGETS:
        raw = (CACHE / (sid + '.bin')).read_bytes()
        assert len(raw) == receipts[sid]['bytes'] and hashlib.sha256(raw).hexdigest() == receipts[sid]['sha256_original']
        originals[sid] = raw
    logic = originals['party-ldp-expansion-logic'].decode()
    assert 'this.jsonRoot="/member/data/"' in logic and '"member.json"' in logic
    assert '参議院議員' in logic and '鳥取県・島根県' in logic and '徳島県・高知県' in logic
    assert '自民党所属議員のプロフィール' in originals['party-ldp-expansion-search'].decode()
    report = dict(schema_version=1, checked_at='2026-10-09',
        note='党公式の現職一覧と議会名簿の正式名・院・選挙区を照合。党籍資料日は未確認。取得日を党籍の基準日にしない。予算決定時点の所属党を意味しない。会派から所属党を推定しない。',
        sources=[], originals=list(receipts.values()), entries=[], issues=[], stats={})
    members = json.loads((ROOT / 'public/data.json').read_text())['legislators']
    for sid in ['party-ldp-expansion', *[f'party-cdp-expansion-{i}' for i in range(1, 5)]]:
        records = ldp_entries(originals[sid]) if sid == 'party-ldp-expansion' else cdp_entries(originals[sid])
        filename = sid + '.txt'
        # Re-encode only inspected public identity fields; omit images, addresses,
        # phones, scripts, analytics, navigation and private local credentials.
        selected = '\n'.join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in records) + '\n'
        (ROOT / 'data/source-text' / filename).write_text(selected)
        report['sources'].append(dict(id=sid, url=TARGETS[sid], filename=filename, party=records[0]['party'],
            as_of=None, checked_at=report['checked_at'], sha256=hashlib.sha256(selected.encode()).hexdigest(),
            method='党公式全国一覧の現職議員ラベル＋正式名＋院＋選挙区', original=receipts[sid],
            supporting_original_ids=['party-ldp-expansion-search', 'party-ldp-expansion-logic'] if sid == 'party-ldp-expansion' else []))
        count = 0
        for line, entry in enumerate(records, 1):
            candidates = [m for m in members if compact(m['name']) == compact(entry['name']) and m['chamber'] == entry['chamber']]
            if len(candidates) != 1 or not district_matches(candidates[0], entry):
                report['issues'].append(dict(source_id=sid, roster_name=entry['name'], roster_chamber=entry['chamber'],
                    status='保留', reason='正式名・院の一意一致、または議会名簿の現職選挙区一致を満たさない'))
                continue
            member = candidates[0]
            report['entries'].append(dict(member_id=member['id'], member_name=member['name'], chamber=member['chamber'],
                district=member['district'], party=entry['party'], party_source=TARGETS[sid], party_as_of=None,
                checked_at=report['checked_at'], source_id=sid, method='党公式全国一覧の現職議員ラベル＋正式名＋院＋選挙区',
                roster_name=entry['name'], roster_reading=entry['reading'], roster_district=entry['prefecture'] or entry['prs'],
                profile_url=entry['profile_url'], original_location=entry['original_location'], excerpt_line=line))
            count += 1
        report['stats'][sid] = dict(extracted=len(records), matched=count)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    validate()


def validate():
    report = json.loads(REPORT.read_text())
    members = {m['id']: m for m in json.loads((ROOT / 'public/data.json').read_text())['legislators']}
    sources = {s['id']: s for s in report['sources']}
    originals = {o['source_id']: o for o in report['originals']}
    assert set(originals) == set(TARGETS)
    assert set(sources) == {'party-ldp-expansion', *[f'party-cdp-expansion-{i}' for i in range(1, 5)]}
    for original in report['originals']:
        assert original['source_id'] in TARGETS and original['url'] == TARGETS[original['source_id']]
        checked(original['url']); checked(original['final_url'])
        assert re.fullmatch(r'[0-9a-f]{64}', original['sha256_original']) and 0 < original['bytes'] <= MAX_BYTES
    for source in sources.values():
        checked(source['url'])
        assert source['url'] == TARGETS[source['id']] and source['original'] == originals[source['id']]
        assert source['party'] == ('自由民主党' if source['id'] == 'party-ldp-expansion' else '立憲民主党')
        assert source['as_of'] is None
        assert re.fullmatch(r'party-(?:ldp|cdp)-expansion(?:-[1-4])?\.txt', source['filename'])
        raw = (ROOT / 'data/source-text' / source['filename']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == source['sha256']
        for line in raw.decode().splitlines():
            entry = json.loads(line)
            assert set(entry) == {'party', 'name', 'reading', 'chamber', 'prefecture', 'prs', 'profile_url', 'original_location', 'status_label'}
            checked(entry['profile_url'])
            assert entry['party'] == source['party']
            assert entry['status_label'] in {'衆議院議員', '参議院議員'}
    seen = set()
    for evidence in report['entries']:
        member = members[evidence['member_id']]
        assert member['name'] == evidence['member_name'] and member['chamber'] == evidence['chamber'] and member['district'] == evidence['district']
        source = sources[evidence['source_id']]
        entry = json.loads((ROOT / 'data/source-text' / source['filename']).read_text().splitlines()[evidence['excerpt_line'] - 1])
        assert compact(member['name']) == compact(entry['name']) and member['chamber'] == entry['chamber'] and district_matches(member, entry)
        assert evidence['party_source'] == source['url'] and evidence['party_as_of'] is None and evidence['party'] == entry['party']
        assert (evidence['member_id'], evidence['source_id']) not in seen
        seen.add((evidence['member_id'], evidence['source_id']))
    print(json.dumps({'sources': len(sources), 'originals': len(report['originals']), 'matched': len(report['entries']), 'held': len(report['issues']), 'stats': report['stats']}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--generate', action='store_true', help='Regenerate reviewed excerpts/report from ignored cached originals')
    args = parser.parse_args()
    if args.fetch:
        fetch()
    if args.fetch or args.generate:
        generate()
    else:
        validate()
