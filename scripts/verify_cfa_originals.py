"""Review first-round CFA facility grant notices; preserve missing municipalities.

--fetch follows only the PDF links on the two official announcement pages.
The first-round lists are reference comparisons, not annual allocations or
matched project cohorts. Cache files and raw pages are never published.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urljoin
from urllib.request import build_opener
from fetch_sources import CheckedRedirect, MAX_BYTES, checked

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/originals'
MANIFEST = ROOT / '.cache/cfa-manifest.json'
OUTPUT = ROOT / 'data/reviewed-cfa.json'
DATE = '2026-10-09'
PAGES = {2025: 'https://www.cfa.go.jp/press/0f21431e-d04e-4f23-a84a-4c2153ac7b4e',
         2026: 'https://www.cfa.go.jp/press/83df6d8f-fc87-48db-b073-546ffa572ead'}
TOTALS = {2025: 10039796, 2026: 9699402}
COUNTS = {2025: 214, 2026: 198}


def path(url):
    return CACHE / (hashlib.sha256(url.encode()).hexdigest() + '.bin')


def fetch():
    receipts = []
    CACHE.mkdir(parents=True, exist_ok=True)
    opener = build_opener(CheckedRedirect())
    for page in PAGES.values():
        pending = [page]
        for url in pending:
            with opener.open(checked(url), timeout=25) as response:
                content = response.read(MAX_BYTES + 1)
                if len(content) > MAX_BYTES:
                    raise ValueError('File exceeds size limit')
                receipt = dict(url=url, final_url=checked(response.geturl()),
                    retrieved_at_utc=datetime.now(timezone.utc).isoformat(),
                    sha256_original=hashlib.sha256(content).hexdigest(), bytes=len(content),
                    content_type=response.headers.get_content_type())
            path(url).write_bytes(content)
            receipts.append(receipt)
            MANIFEST.write_text(json.dumps(receipts, ensure_ascii=False, indent=2) + '\n')
            if url == page:
                links = list(dict.fromkeys(re.findall(r'href="([^"]+\.pdf)"', content.decode('utf-8'))))
                assert len(links) == 1, 'Announcement must identify exactly one notice PDF'
                pending.append(checked(urljoin(page, links[0])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    if parser.parse_args().fetch:
        fetch()
    receipts = json.loads(MANIFEST.read_text())
    assert len(receipts) == 4 and len({r['url'] for r in receipts}) == 4
    by_url = {r['url']: r for r in receipts}
    for receipt in receipts:
        content = path(receipt['url']).read_bytes()
        assert len(content) == receipt['bytes']
        assert hashlib.sha256(content).hexdigest() == receipt['sha256_original']
        checked(receipt['url']); checked(receipt['final_url'])
    sources, tables, originals = [], {}, []
    for year, page in PAGES.items():
        html = path(page).read_text()
        title = f'令和{year-2018}年度子ども・子育て支援施設整備交付金の内示について'
        assert title in html and f'令和{year-2018}年4月1日' in html
        links = list(dict.fromkeys(re.findall(r'href="([^"]+\.pdf)"', html)))
        assert len(links) == 1
        pdf = checked(urljoin(page, links[0]))
        assert pdf in by_url
        text = subprocess.run(['pdftotext', '-layout', str(path(pdf)), '-'],
                              capture_output=True, text=True, check=True).stdout
        assert f'令和{chr(0xff10+year-2018)}年度' in text and '（第１次）' in text
        assert '単位：千円' in text
        entries = re.findall(r'(?:^|\s)(\d+)\s+([^\s\d]+(?:県|府|都|道)[^\s\d]+)\s+([\d,]+)', text)
        assert len(entries) == COUNTS[year]
        assert {int(i) for i, _, _ in entries} == set(range(1, COUNTS[year]+1))
        values = {name: int(amount.replace(',', '')) for _, name, amount in entries}
        assert len(values) == len(entries)
        total = re.findall(r'合計\s+([\d,]+)', text)
        assert len(total) == 1 and int(total[0].replace(',', '')) == TOTALS[year]
        assert sum(values.values()) == TOTALS[year]
        tables[year] = values
        excerpt = '\n'.join(['単位：千円。第1次内示の掲載自治体のみ。', '|自治体|内示額|', '|---|---:|'] +
                             [f'|{name}|{amount:,}|' for name, amount in sorted(values.items())] +
                             [f'|合計|{TOTALS[year]:,}|'])
        for sid, url, body, locator in [
            (f'cfa-first-{year}', pdf, excerpt, 'PDF全頁の自治体名・内示額（千円）'),
            (f'cfa-first-announcement-{year}', page, title+'（第1次）。公開日：'+f'{year}年4月1日。別添PDFを掲載。', '発表題名・公開日・別添リンク')]:
            sources.append(dict(id=sid, title=f'{year}年度子ども・子育て支援施設整備交付金・第1次内示'+('発表' if url==page else '一覧'),
                url=url, excerpt=body, locator=locator, kind='予算・議員資料',
                accessed=DATE, published=f'{year}-04-01', retrieved_via='公式頁・掲載PDF原本を直接取得し照合',
                sha256_extracted_text=hashlib.sha256(body.encode()).hexdigest()))
            receipt = deepcopy(by_url[url]); receipt['source_ids'] = [sid]
            receipt['verification'] = dict(status='downloaded_only', checked_at=DATE,
                method='公式発表題名・公開日・掲載PDFリンクを確認。', locator=locator, row_ids=[], fields=[])
            originals.append(receipt)
    prefectures = json.loads((ROOT / 'public/data.json').read_text())['prefectures']
    note = ('両年度4月1日の第1次内示掲載額。年度全体・当初予算総額・交付決定額・実支出ではない。'
            '内示対象の事業・件数、財源年度や補正の扱い、2026年4月7日予算成立前の通知との関係は未照合のため参考比較。'
            '片年度非掲載は追加回への移行や対象変更の可能性があり、ゼロ・廃止とは認定しない。'
            '千円を百万円へ換算。市町村名から都道府県を確認し、県内全議員を表示するが当該市町村選挙区との直接対応ではない。')
    rows = []
    for region in sorted(set(tables[2025]) | set(tables[2026])):
        prefs = [p for p in prefectures if region.startswith(p)]
        assert len(prefs) == 1
        amounts = {f'amount{year}': tables[year].get(region)/1000 if region in tables[year] else None for year in (2025,2026)}
        rows.append(dict(id='cfa-first-'+hashlib.sha256(region.encode()).hexdigest()[:16], ministry='内閣府',
            agency='こども家庭庁', program='子ども・子育て支援施設整備交付金', region=region, prefecture=prefs[0],
            basis='第1次内示（参考）', account='財源区分未確認', unit='百万円',
            scope='掲載自治体別内示額', period2025='2025年4月1日第1次内示', period2026='2026年4月1日第1次内示',
            source_ids=['cfa-first-2025','cfa-first-2026','cfa-first-announcement-2025','cfa-first-announcement-2026'],
            evidence_status='公式原本集計済み', note=note, **amounts,
            comparability='片年度非掲載' if None in amounts.values() else '参考・第1次内示（対象・財源未照合）'))
    for receipt in originals:
        if receipt['source_ids'][0].startswith('cfa-first-announcement'):
            continue
        year = int(receipt['source_ids'][0][-4:]); field = f'amount{year}'
        receipt['verification'].update(status='matched', fields=[field], row_ids=[r['id'] for r in rows],
            values={r['id']: {field: r[field]} for r in rows},
            method='全掲載自治体番号の連続性・一意性を検査。各内示額を千円整数で抽出し公表総額と完全一致。非掲載はnull。')
    report = dict(schema_version=1, checked_at=DATE, sources=sources, originals=originals, rows=rows,
        totals_thousand_yen={str(y): TOTALS[y] for y in TOTALS}, listed_municipalities={str(y): COUNTS[y] for y in COUNTS},
        research_notes=[dict(ministry='内閣府', agency='こども家庭庁', status='第1次内示を参考収載・年度全体未確認',
            note=note, source_ids=[s['id'] for s in sources])])
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(f'PASS: CFA {len(rows)} municipalities, 214/198 listed, exact official totals, 4 originals')


if __name__ == '__main__':
    main()
