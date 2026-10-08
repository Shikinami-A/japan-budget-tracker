"""Re-extract reviewed MLIT original PDF tables; use --fetch to download official files."""
import argparse
import hashlib
import json
import re
import subprocess
import time
import unicodedata
from pathlib import Path
from urllib.request import Request, build_opener

from fetch_sources import CheckedRedirect, MAX_BYTES, checked

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache' / 'originals'
OUT = ROOT / 'data' / 'reviewed-mlit.json'
CHECKED_AT = '2026-10-09'
PREFS = '北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 栃木県 群馬県 埼玉県 千葉県 東京都 神奈川県 新潟県 富山県 石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県 三重県 滋賀県 京都府 大阪府 兵庫県 奈良県 和歌山県 鳥取県 島根県 岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県'.split()
BUREAUS = ['北海道開発局', *[p + '地方整備局' for p in ['東北', '関東', '北陸', '中部', '近畿', '中国', '四国', '九州']], '沖縄総合事務局']
PDFS = {
    2025: 'https://www.mlit.go.jp/report/press/content/001881404.pdf',
    2026: 'https://www.mlit.go.jp/report/press/content/001994794.pdf',
    'inquiry': 'https://www.mlit.go.jp/road/content/002025983.pdf',
}
ENTRY_URLS = [
    'https://www.mlit.go.jp/page/kanbo05_hy_003263.html',
    'https://www.mlit.go.jp/page/kanbo05_hy_003334.html',
    'https://www.mlit.go.jp/page/kanbo05_hy_003321.html',
    'https://www.mlit.go.jp/page/kanbo05_hy_003397.html',
    'https://www.mlit.go.jp/report/press/kanbo05_hh_000289.html',
    'https://www.mlit.go.jp/report/press/kanbo05_hh_000304.html',
]


def original_path(url):
    return CACHE / (hashlib.sha256(url.encode()).hexdigest() + '.bin')


def fetch(url):
    path = original_path(url)
    if path.exists():
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    receipt = {'url': url, 'retrieved_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    with build_opener(CheckedRedirect()).open(Request(checked(url), headers={
        'User-Agent': 'japan-budget-tracker/0.1 (public official budget research)'}), timeout=30) as response:
        size = response.headers.get('Content-Length')
        if size and int(size) > MAX_BYTES:
            raise ValueError('File exceeds size limit')
        content = response.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError('File exceeds size limit')
        receipt.update(status='downloaded', bytes=len(content),
                       sha256_original=hashlib.sha256(content).hexdigest(),
                       final_url=checked(response.geturl()), content_type=response.headers.get_content_type())
        path.write_bytes(content)
    with (CACHE / 'manifest.jsonl').open('a') as stream:
        stream.write(json.dumps(receipt, ensure_ascii=False) + '\n')


def receipt_for(url):
    receipts = [json.loads(line) for line in (CACHE / 'manifest.jsonl').read_text().splitlines()]
    receipt = next(r for r in reversed(receipts) if r['url'] == url and r['status'] == 'downloaded')
    content = original_path(url).read_bytes()
    assert hashlib.sha256(content).hexdigest() == receipt['sha256_original'], url
    assert len(content) == receipt['bytes'], url
    return {k: receipt[k] for k in ['url', 'final_url', 'retrieved_at_utc', 'sha256_original', 'bytes', 'content_type']}


def pdf_pages(url):
    receipt_for(url)
    path = original_path(url)
    target = path.with_suffix('.txt')
    subprocess.run(['pdftotext', '-layout', str(path), str(target)], check=True)
    return target.read_text().split('\f')


def allocation_table(page, names):
    assert '単位：百万円' in page
    assert '事業費ベース' in page
    records = {}
    for line in page.splitlines():
        match = re.match(r'^\s*([^\d]+?)\s+(\d[\d,]*|-)\s+(\d[\d,]*|-)\s+(\d[\d,]*|-)\s+', line)
        if not match:
            continue
        name = re.sub(r'\s+', '', match[1])
        if name not in names + ['合計']:
            continue
        assert name not in records, name
        # Only the current-year allocation columns, never zero-debt commitments.
        records[name] = [None if v == '-' else int(v.replace(',', '')) for v in match.groups()[1:]]
    assert set(records) == set(names) | {'合計'}, set(records)
    for name, values in records.items():
        assert values[0] is not None and values[2] is not None, name
        # Rounded first/second components can differ by one from published total.
        assert abs(values[0] + (values[1] or 0) - values[2]) <= 1, name
    assert abs(sum(records[n][2] for n in names) - records['合計'][2]) <= len(names), records['合計']
    return records


def inquiry_values(page):
    assert '別添' in page and '国費：百万円' in page
    records = {}
    municipality = None
    for line in page.splitlines():
        match = re.match(r'^\s*(那須烏山市|那珂川町|栃木県)\s+(\d+)\s+(\d+)\s+([\d.]+)\s+(\d+)\s+([\d.]+)\s+([+△][\d.]+)%', line)
        if not match:
            continue
        label = match[1]
        if label != '栃木県' and (label, '合計') not in records:
            municipality = label
            scope = '合計'
        else:
            assert municipality is not None
            scope = '県事業' if label == '栃木県' else '市町事業'
        records[(municipality, scope)] = [int(match[3]), int(match[5]), float(match[7].replace('△', '-'))]
    assert len(records) == 6, records
    return records


def build():
    rows, sources, originals, totals = [], [], [], {}
    for year in [2025, 2026]:
        pages = pdf_pages(PDFS[year])
        # Require the same current fiscal-year columns on both tables.
        for page in pages[3:5]:
            assert f'令和{year - 2018}年度配分額' in re.sub(r'\s+', '', unicodedata.normalize('NFKC', page))
        press_url = ENTRY_URLS[4 if year == 2025 else 5]
        press_html = original_path(press_url).read_text()
        assert ('令和7年4月1日' if year == 2025 else '令和8年4月7日') in press_html
        assert PDFS[year].replace('https://www.mlit.go.jp', '') in press_html
        direct = allocation_table(pages[3], BUREAUS)
        subsidies = allocation_table(pages[4], PREFS)
        totals[str(year)] = {'direct_published_total': direct['合計'][2],
                        'subsidy_published_total': subsidies['合計'][2],
                        'subsidy_sum_rounded_rows': sum(subsidies[p][2] for p in PREFS)}
        source_id = f'mlit-road-allocation-{year}'
        excerpt = '\f'.join(pages[3:5]).strip()
        sources.append(dict(id=source_id, title=f'{year}年度道路関係予算当初配分概要（事業費・国費ではない）',
                            url=PDFS[year], locator='PDF4〜5頁／本文3〜4頁：都道府県別等配分額、令和当該年度配分額の計欄、百万円、事業費ベース。国庫債務負担行為は除外。',
                            kind='予算・地域配分資料', accessed=CHECKED_AT,
                            published='2025-04-01' if year == 2025 else '2026-04-07',
                            document_date=f'{year}-04', document_date_precision='month',
                            published_date_evidence='同年度当初配分報道発表ページ',
                            retrieved_via='公式PDF原本をTLS検証付きHTTPSで取得、pdftotext -layoutで該当表抽出',
                            sha256_extracted_text=hashlib.sha256(excerpt.encode()).hexdigest(), excerpt=excerpt))
        originals.append(dict(**receipt_for(PDFS[year]), source_ids=[source_id],
                              verification=dict(status='matched', checked_at=CHECKED_AT,
                                                method='pdftotext -layoutで計欄を抽出、47県/10局の一意性・同範囲・単位・丸めを検査',
                                                locator='PDF4〜5頁／本文3〜4頁',
                                                row_ids=[f'mlit-road-subsidy-{p}' for p in PREFS] + [f'mlit-road-direct-{p}' for p in BUREAUS],
                                                fields=[f'amount{year}'])))
        for names, table, program, prefix, scope in [
            (PREFS, subsidies, '道路関係補助事業（事業費・国費ではない）', 'subsidy', '都道府県別配分額（事業費）'),
            (BUREAUS, direct, '道路関係直轄事業（事業費・国費ではない）', 'direct', '地方支分部局別配分額（事業費）'),
        ]:
            for name in names:
                row_id = f'mlit-road-{prefix}-{name}'
                record = next((r for r in rows if r['id'] == row_id), None)
                if record is None:
                    record = dict(id=row_id, ministry='国土交通省', program=program, region=name,
                                  prefecture=name if name in PREFS else None,
                                  basis='当初配分（事業費）', account='会計別未分解', unit='百万円',
                                  source_ids=['mlit-road-allocation-2025', 'mlit-road-allocation-2026'],
                                  period2025='2025年度当初配分', period2026='2026年度当初配分',
                                  evidence_status='公式原本照合済み', comparability='同範囲', scope=scope,
                                  precision='百万円に丸めた表示値',
                                  note='地方負担を含む事業費であり、国費・社会資本整備総合交付金（道路）の想定国費とは合算しない。令和当該年度配分額の計欄を比較し、ゼロ国債の国庫債務負担行為は含めない。調査費等は表の対象外。各計数は丸められ、合計と内訳の単純合計が一致しない場合がある。' +
                                  ('直轄道路の災害復旧事業費、諸費等は別枠。地方整備局等の複数県の管内配分であり、都道府県に割り当てない。' if name in BUREAUS else '当該県内の補助事業配分であり、県自身だけの受取額とは扱わない。'))
                    rows.append(record)
                record[f'amount{year}'] = table[name][2]
                record[f'headquarters_allocation{year}'] = table[name][0]
                record[f'block_allocation{year}'] = table[name][1]
    pages = pdf_pages(PDFS['inquiry'])
    inquiry = inquiry_values(pages[2])
    snapshot = json.loads((ROOT / 'public' / 'data.json').read_text())
    scope_names = ['市町事業', '県事業', '合計']
    for municipality in ['那須烏山市', '那珂川町']:
        for i, scope in enumerate(scope_names):
            existing = next(r for r in snapshot['rows'] if r['id'] == f'road-{municipality}-{i}')
            assert inquiry[(municipality, scope)] == [existing['amount2025'], existing['amount2026'], existing['published_change_pct']]
    originals.append(dict(**receipt_for(PDFS['inquiry']), source_ids=['mlit-inquiry'],
                          verification=dict(status='matched', checked_at=CHECKED_AT,
                                            method='PDF別添の2025/2026想定国費と公表伸率を既存6行へ照合。丸め表示の合計から伸率を再計算しない。',
                                            locator='PDF3頁、別添、国費：百万円',
                                            row_ids=[f'road-{p}-{i}' for p in ['那須烏山市', '那珂川町'] for i in range(3)],
                                            fields=['amount2025', 'amount2026', 'published_change_pct'])))
    for url in ENTRY_URLS:
        originals.append(dict(**receipt_for(url), source_ids=[], verification=dict(
            status='retrieved', checked_at=CHECKED_AT, method='公式入口HTML原本のハッシュ・サイズを照合。年度別当初配分と箇所表へのリンクを確認。', locator='HTML', row_ids=[], fields=[])))
    assert len(rows) == 57
    # Source tables carry business cost, while the inquiry appendix carries national cost.
    return dict(schema_version=1, checked_at=CHECKED_AT, sources=sources, rows=rows,
                originals=originals, verification_summary=dict(new_rows=57, prefectures=47, bureaus=10,
                                                               existing_inquiry_rows_matched=6, published_totals=totals),
                limitations=['道路局直轄・補助の当初配分は事業費ベース。道路交付金の全国要素事業の想定国費・事業箇所別比較は未集計。',
                             '補助事業47県と直轄事業10局は異なる地域範囲。局管内を都道府県に割り振らない。',
                             '精査PDFの資料日付は本文から確認できず。既存の公表日2026-10-06の正否はこの金額照合に含めない。'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true', help='Fetch missing approved official originals through the inherited proxy')
    parser.add_argument('--write', action='store_true', help='Write reviewed JSON; default checks committed JSON against originals')
    args = parser.parse_args()
    if args.fetch:
        for url in list(PDFS.values()) + ENTRY_URLS:
            fetch(url)
    result = build()
    if args.write:
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    else:
        assert json.loads(OUT.read_text()) == result, 'Reviewed data differs from original re-extraction'
    print(json.dumps(result['verification_summary'], ensure_ascii=False))


if __name__ == '__main__':
    main()
