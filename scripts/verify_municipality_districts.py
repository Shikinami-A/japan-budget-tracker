"""Verify municipality/district evidence from Tochigi's official election pages.

Uses the 2026 election result table and the prefectural electoral-system PDF.
The single split municipality retains both districts and the old-area boundary.
No municipality codes or budget-decision-date incumbents are inferred.
"""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from verify_party_expansion import DOM, Node, compact
from verify_party_followup import fetch_reviewed_originals

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/party-followup'
REPORT = ROOT / 'data/reviewed-municipality-districts.json'
TARGETS = {
    'tochigi-district-final': 'https://www.pref.tochigi.lg.jp/senkyo/r08syugi/file/BH_KAIHYO.html',
    'tochigi-map': 'https://www.pref.tochigi.lg.jp/senkyo/r05kengi/gaiyou/senkyomap.pdf',
}


def checked(url):
    p = urlsplit(url)
    assert p.scheme == 'https' and p.hostname == 'www.pref.tochigi.lg.jp'
    assert not p.username and not p.password and p.port in (None, 443)
    assert url in TARGETS.values()
    return url


def original(key):
    receipt = json.loads((CACHE / (key + '.receipt.json')).read_text())
    raw = (CACHE / (key + '.bin')).read_bytes()
    assert receipt['url'] == TARGETS[key] and len(raw) == receipt['bytes']
    assert hashlib.sha256(raw).hexdigest() == receipt['sha256_original']
    return raw, receipt


def descendants(node, tag):
    return [node] if node.tag == tag else [y for x in node.children if isinstance(x, Node) for y in descendants(x, tag)]


def generate():
    raw, result_receipt = original('tochigi-district-final')
    dom = DOM(); dom.feed(raw.decode('cp932'))
    assert '令和8年2月8日' in dom.root.text() and '衆議院' in dom.root.text()
    records = []
    for index, tr in enumerate(descendants(dom.root, 'tr')):
        cells = [x.text().strip() for x in tr.children if isinstance(x, Node) and x.tag in {'td', 'th'}]
        if len(cells) < 3 or cells[0] not in {'1', '2', '3', '4', '5'}:
            continue
        assert cells[2] == '*'  # Final, confirmed result row.
        name = cells[1]
        assert re.fullmatch(r'.+[市町](?:第[１２])?', name)
        records.append(dict(prefecture='栃木県', municipality_label=name,
            district='栃木' + cells[0], original_location=f'HTML table tr[{index}] cells[0:3] 選挙区/団体名/確定表示'))
    assert len(records) == 26
    _, map_receipt = original('tochigi-map')
    mapped = subprocess.check_output(['pdftotext', '-layout', str(CACHE / 'tochigi-map.bin'), '-']).decode()
    boundaries = []
    for number, value in re.findall(r'^第\s*([１-５])\s*区\s+(.+)$', mapped, re.M):
        boundaries.append(dict(district='栃木' + compact(number), area=value.strip(), original_location='PDF p.2 小選挙区選挙 第1〜5区の区域表'))
    assert len(boundaries) == 5
    assert boundaries[0]['area'] == '宇都宮市のうち旧宇都宮市の区域、河内郡'
    assert boundaries[1]['area'] == '宇都宮市のうち旧上河内町・旧河内町の区域、鹿沼市、日光市、さくら市、塩谷郡'
    sources = []
    for sid, receipt, excerpt in [
        ('municipality-tochigi-2026', result_receipt, records),
        ('municipality-tochigi-boundaries', map_receipt, boundaries),
    ]:
        filename = sid + '.txt'; text = '\n'.join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in excerpt) + '\n'
        (ROOT / 'data/source-text' / filename).write_text(text)
        sources.append(dict(id=sid, url=receipt['url'], filename=filename,
            as_of='2026-02-08' if sid.endswith('2026') else None, checked_at='2026-10-09',
            sha256=hashlib.sha256(text.encode()).hexdigest(), original=receipt,
            title='栃木県選管：2026年衆院選の市町・小選挙区対応' if sid.endswith('2026') else '栃木県選管：選挙制度と小選挙区区域',
            method='確定結果表の選挙区・団体名欄' if sid.endswith('2026') else 'PDF第2頁の小選挙区区域表。人口集計日を境界の基準日にしない。'))
    mappings = []
    by_name = {}
    for line, r in enumerate(records, 1):
        name = re.sub(r'第[１２]$', '', r['municipality_label'])
        by_name.setdefault(name, []).append((line, r))
    for name, parts in by_name.items():
        split = len(parts) > 1
        boundaries_for_city = [dict(district='栃木1', area='旧宇都宮市の区域'),
            dict(district='栃木2', area='旧上河内町・旧河内町の区域')] if split else []
        mappings.append(dict(prefecture='栃木県', municipality=name, municipality_code=None,
            chamber='衆議院', election_type='小選挙区', districts=sorted({r['district'] for _, r in parts}),
            coverage='municipality_split' if split else 'municipality_whole',
            boundary_detail=boundaries_for_city if split else None,
            as_of='2026-02-08', source_id='municipality-tochigi-2026',
            evidence=[dict(source_id='municipality-tochigi-2026', excerpt_line=line,
                original_location=r['original_location'], municipality_label=r['municipality_label']) for line, r in parts],
            boundary_source_id='municipality-tochigi-boundaries' if split else None,
            boundary_excerpt_lines=[1, 2] if split else [],
            note='市全体を一人に割り当てない。予算配分決定時点の議員・境界の確認とは別。' if split else '2026年衆院選時点の市町域対応。予算配分決定時点の議員との一致は未確認。'))
    assert len(mappings) == 25 and sum(m['coverage'] == 'municipality_split' for m in mappings) == 1
    report = dict(schema_version=1, checked_at='2026-10-09',
        note='県選管の公式原本による市町と小選挙区の対応。市町村コードは未照合のためnull。2026年選挙時点と配分決定時点を区別。県内全議員の一覧を市町の担当議員とみなさない。',
        sources=sources, originals=[result_receipt, map_receipt], mappings=mappings,
        issues=[dict(status='未照合', scope='栃木県以外の市町村・自治体コード・2025/2026予算配分決定時点', reason='今回確認した公式原本の範囲外')],
        stats=dict(municipalities=25, whole_municipalities=24, split_municipalities=1))
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    validate()


def validate():
    report = json.loads(REPORT.read_text()); sources = {s['id']: s for s in report['sources']}; excerpts = {}
    for s in sources.values():
        p = urlsplit(s['url']); assert p.scheme == 'https' and p.hostname == 'www.pref.tochigi.lg.jp'
        assert not p.username and not p.password and p.port in (None, 443)
        assert s['url'] in TARGETS.values() and re.fullmatch(r'municipality-[a-z0-9-]+\.txt', s['filename'])
        text = (ROOT / 'data/source-text' / s['filename']).read_bytes()
        assert hashlib.sha256(text).hexdigest() == s['sha256']
        assert s['original']['download_status'] == 'downloaded'
        excerpts[s['id']] = [json.loads(line) for line in text.decode().splitlines()]
    seen = set()
    for m in report['mappings']:
        key = (m['prefecture'], m['municipality']); assert key not in seen; seen.add(key)
        assert m['municipality_code'] is None and m['as_of'] == '2026-02-08'
        assert m['chamber'] == '衆議院' and m['election_type'] == '小選挙区'
        expected = set()
        for e in m['evidence']:
            row = excerpts[e['source_id']][e['excerpt_line'] - 1]
            assert row['municipality_label'] == e['municipality_label'] and row['original_location'] == e['original_location']
            assert re.sub(r'第[１２]$', '', row['municipality_label']) == m['municipality']
            expected.add(row['district'])
        assert expected == set(m['districts'])
        if m['coverage'] == 'municipality_whole':
            assert len(expected) == 1 and m['boundary_detail'] is None
        else:
            assert m['municipality'] == '宇都宮市' and expected == {'栃木1', '栃木2'}
            assert {b['district'] for b in m['boundary_detail']} == expected
            for b, line in zip(m['boundary_detail'], m['boundary_excerpt_lines']):
                row = excerpts[m['boundary_source_id']][line - 1]
                assert row['district'] == b['district'] and b['area'] in row['area']
    assert report['stats'] == dict(municipalities=25, whole_municipalities=24, split_municipalities=1)
    print(json.dumps(report['stats'], ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--fetch', action='store_true'); parser.add_argument('--generate', action='store_true'); args = parser.parse_args()
    if args.fetch:
        fetch_reviewed_originals(REPORT, checked)
    if args.generate:
        generate()
    elif not args.fetch:
        validate()
