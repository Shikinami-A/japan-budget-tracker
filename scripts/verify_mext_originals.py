"""Review MEXT initial-budget grant breakdowns; --fetch downloads originals.

Requires pdftotext. Institution allocations are never attributed to prefectures
from a university's name or headquarters address. This is an appropriation's
initial-budget cost breakdown, not a payment, final institutional budget, or
outturn. Own revenue and facilities grants in appendix tables are excluded.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import html as html_entities
import json
from pathlib import Path
import re
import subprocess
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/originals'
MANIFEST = ROOT / '.cache/mext-manifest.json'
OUTPUT = ROOT / 'data/reviewed-mext.json'
CHECKED_AT = '2026-10-09'
PDFS = {
    2025: 'https://www.mext.go.jp/content/20250414-mxt_kaikesou01-000041735_1.pdf',
    2026: 'https://www.mext.go.jp/content/20260224-mxt_kaikesou01-000047641_1.pdf',
}
PERFORMANCE = {
    2025: 'https://www.mext.go.jp/content/20260603-mxt_hojinka-000024750_1.pdf',
    2026: 'https://www.mext.go.jp/content/20250603-mxt_hojinka-100014170_1.pdf',
}
ENTRIES = [
    'https://www.mext.go.jp/a_menu/yosan/r01/1413361_00013.htm',
    'https://www.mext.go.jp/a_menu/yosan/r01/1413361_00018.htm',
    'https://www.mext.go.jp/a_menu/yosan/r01/1420672_00001.html',
    'https://www.mext.go.jp/a_menu/koutou/houjin/1417427.htm',
    'https://www.mext.go.jp/a_menu/koutou/houjin/mext_02008.html',
]
PREFIXES = ('国立大学法人', '大学共同利用機関法人', '機能強化特別支援等事業')
TOTALS = {2025: 1078350085, 2026: 1097136487}


def path_for(url):
    return CACHE / (hashlib.sha256(url.encode()).hexdigest() + '.bin')


def checked_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname != 'www.mext.go.jp' or parsed.username or parsed.password:
        raise ValueError('Only official MEXT HTTPS resources are allowed')
    return url


class MextRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, checked_url(newurl))


def fetch():
    CACHE.mkdir(parents=True, exist_ok=True)
    old = json.loads(MANIFEST.read_text())['originals'] if MANIFEST.exists() else []
    receipts = {r['url']: r for r in old}
    opener = urllib.request.build_opener(MextRedirect())
    for url in [*PDFS.values(), *PERFORMANCE.values(), *ENTRIES]:
        if url in receipts and path_for(url).exists():
            continue
        with opener.open(checked_url(url), timeout=60) as response:
            content = response.read(32 * 1024 * 1024 + 1)
            if len(content) > 32 * 1024 * 1024:
                raise ValueError('Original exceeds 32 MiB')
            receipt = dict(url=url, final_url=checked_url(response.url),
                           retrieved_at_utc=datetime.now(timezone.utc).isoformat(),
                           sha256_original=hashlib.sha256(content).hexdigest(),
                           bytes=len(content), content_type=response.headers.get_content_type())
        path_for(url).write_bytes(content)
        receipts[url] = receipt
        MANIFEST.write_text(json.dumps({'originals': list(receipts.values())}, ensure_ascii=False, indent=2) + '\n')


def pages_for(url):
    target = path_for(url).with_suffix('.txt')
    subprocess.run(['pdftotext', '-layout', str(path_for(url)), str(target)], check=True)
    return target.read_text().split('\f')


def institution_breakdown(year, pages):
    first = next(i for i, p in enumerate(pages) if '国立大学法人北海道大学' in p)
    last = next(i for i, p in enumerate(pages[first:], first) if '機能強化特別支援等事業' in p)
    names, values = [], []
    for i in range(first, last + 1):
        lines = pages[i].splitlines()
        # Only the far-right 積算内訳 column; exclude classification/item columns.
        column = min(len(line) - len(line.lstrip()) for line in lines
                     if line.lstrip().startswith(PREFIXES) and len(line) - len(line.lstrip()) > 80)
        active = False
        for line in lines:
            text = line[column:].strip()
            if text.startswith(PREFIXES):
                active = True
                names.append(dict(name='', pdf_page=i + 1))
            if not active or not text:
                continue
            number = re.search(r'\d[\d,\s]*', text)
            if number:
                values.append(int(re.sub(r'\D', '', number[0])))
                text = text[:number.start()]
            names[-1]['name'] += re.sub(r'\s+', '', text)
            if names[-1]['name'] == PREFIXES[-1] and len(names) == len(values):
                break
    # PDF extraction sometimes puts the first amount after the next name. Parse
    # name and amount streams separately, preserving each stream's table order.
    assert len(names) == len(values) == 86
    assert len({n['name'] for n in names}) == 86
    records = {n['name']: dict(n, amount_thousand_yen=v) for n, v in zip(names, values)}
    assert sum(values) == TOTALS[year]
    assert str(TOTALS[year]) in re.sub(r'[\s,]', '', pages[first])
    assert '千円' in pages[first]
    # Independently cross-check all 85 named entities against appendix grant
    # income, never total income, university spending, or facility subsidy.
    appendix = {}
    for i, page in enumerate(pages):
        match = re.search(r'\(\s*\d+\s*\)\s*((?:国立大学法人|大学共同利用機関法人)[^\n]+)', page)
        if not match:
            continue
        name = re.sub(r'\s+', '', match[1])
        if name not in records:
            continue
        grant = next(line for line in page.splitlines()
                     if re.match(r'\s*運\s*営\s*費\s*交\s*付\s*金', line))
        value = int(re.search(r'\d[\d,]+', grant)[0].replace(',', ''))
        assert value == records[name]['amount_thousand_yen'], (year, name, value)
        assert '相当に異動することがある' in page
        assert name not in appendix
        appendix[name] = i + 1
        records[name]['appendix_pdf_page'] = i + 1
    assert len(appendix) == 85
    return records, (first + 1, last + 1)


def source(sid, title, url, locator, excerpt):
    return dict(id=sid, title=title, url=url, locator=locator, kind='予算・機関別資料',
                accessed=CHECKED_AT, published=None,
                retrieved_via='文部科学省公式原本をTLS検証付きHTTPSで取得、pdftotext -layoutで表を抽出',
                sha256_extracted_text=hashlib.sha256(excerpt.encode()).hexdigest(), excerpt=excerpt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    if parser.parse_args().fetch:
        fetch()
    originals = json.loads(MANIFEST.read_text())['originals']
    assert {r['url'] for r in originals} == set([*PDFS.values(), *PERFORMANCE.values(), *ENTRIES])
    for receipt in originals:
        content = path_for(receipt['url']).read_bytes()
        assert len(content) == receipt['bytes']
        assert hashlib.sha256(content).hexdigest() == receipt['sha256_original']
        receipt['source_ids'] = []
        receipt['verification'] = dict(status='downloaded_only', checked_at=CHECKED_AT,
                                        method='公式原本・入口の取得とSHA-256検証。比較額の照合対象ではない。',
                                        locator=receipt['url'].rsplit('/', 1)[1], row_ids=[], fields=[])
    by_url = {r['url']: r for r in originals}
    all_records, sources = {}, []
    for year in (2025, 2026):
        pages = pages_for(PDFS[year])
        all_records[year], bounds = institution_breakdown(year, pages)
        if year == 2025:
            assert '修正成立' in pages[0]
        sid = f'mext-university-initial-{year}'
        excerpt = '\n'.join(['|法人・枠|当初予算積算額（千円）|', '|---|---:|'] +
                            [f'|{name}|{record["amount_thousand_yen"]:,}|' for name, record in all_records[year].items()] +
                            [f'|国立大学法人運営費交付金計|{TOTALS[year]:,}|'])
        sources.append(source(sid, f'{year}年度文部科学省一般会計歳出予算各目明細書' + ('（修正成立）' if year == 2025 else ''),
                              PDFS[year], f'PDF{bounds[0]}〜{bounds[1]}頁：国立大学法人運営費交付金の積算内訳。85法人は後半の年度計画予算見積りの交付金収入欄でも照合。単位千円。', excerpt))
    assert set(all_records[2025]) == set(all_records[2026])
    rows = []
    for i, name in enumerate(all_records[2025]):
        row_id = f'mext-university-initial-{i}'
        pool = name == PREFIXES[-1]
        note = ('国立大学法人運営費交付金の当初予算積算内訳。交付決定額・支出済額ではない。'
                '年度計画予算見積りは法人で予算決定前等のため相当に異動する場合がある旨、原本に明記。'
                '法人単位であり複数大学・キャンパス等の地域配分は未確認。県への帰属を推定しない。')
        if pool:
            note = '機能強化特別支援等事業の未法人配分枠。個別法人配分済額として扱わない。年度計画予算見積りとの法人別照合対象外。'
        rows.append(dict(id=row_id, ministry='文部科学省', program=f'国立大学法人運営費交付金（当初予算積算内訳）／{name}',
                         institution=None if pool else name, region='全国', prefecture=None,
                         basis='当初予算積算内訳（法人・枠別）', account='一般会計', unit='百万円',
                         amount2025=all_records[2025][name]['amount_thousand_yen'] / 1000,
                         amount2026=all_records[2026][name]['amount_thousand_yen'] / 1000,
                         period2025='2025年度', period2026='2026年度', comparability='同範囲',
                         evidence_status='原本数値照合済み', source_ids=[f'mext-university-initial-{y}' for y in (2025, 2026)],
                         geography_status='法人別・地域帰属未確認' if not pool else '未法人配分枠', note=note,
                         source_locators={str(y): all_records[y][name] for y in (2025, 2026)}))
    for year in (2025, 2026):
        original = by_url[PDFS[year]]
        original['source_ids'] = [f'mext-university-initial-{year}']
        original['verification'] = dict(status='matched', checked_at=CHECKED_AT,
                                        method='81国立大学法人＋4大学共同利用機関法人＋未法人配分枠の86積算内訳を抽出。85法人を年度計画予算見積りの交付金収入欄でも照合。86額の合計を交付金目総額と照合。',
                                        locator=sources[year - 2025]['locator'], row_ids=[r['id'] for r in rows],
                                        fields=[f'amount{year}'], values={r['id']: {f'amount{year}': r[f'amount{year}']} for r in rows})
        pages = pages_for(PERFORMANCE[year])
        assert str(year - 2018) in pages[0] or ('８' if year == 2026 else '７') in pages[0]
        sid = f'mext-university-performance-{year}'
        sources.append(source(sid, f'{year}年度成果を中心とする実績状況に基づく配分の説明', PERFORMANCE[year],
                              'PDF1頁：配分対象経費1,000億円は運営費交付金の一部分。全ページは指標・算定方法等で法人別金額表ではない。', pages[0]))
        by_url[PERFORMANCE[year]]['source_ids'] = [sid]
    entry_sources = [('mext-university-entry-2025', ENTRIES[0]), ('mext-university-entry-2026', ENTRIES[1]), ('mext-budget-enacted-2026', ENTRIES[2])]
    for sid, url in entry_sources:
        html = path_for(url).read_text()
        text = re.sub(r'\s+', ' ', html_entities.unescape(re.sub('<[^>]*>', ' ', html)))
        if sid.endswith('2025'):
            assert '修正成立' in text and urlsplit(PDFS[2025]).path in html
            excerpt = re.search(r'〔一般会計〕.{0,700}?〔特別会計〕', text)[0]
        elif sid == 'mext-university-entry-2026':
            assert urlsplit(PDFS[2026]).path in html
            excerpt = re.search(r'〔一般会計〕.{0,700}?〔特別会計〕', text)[0]
        else:
            assert '令和8年4月7日' in text and '案のとおり成立' in text
            excerpt = re.search(r'当初予算.{0,160}?令和8年4月7日.{0,30}', text)[0]
        sources.append(source(sid, '文部科学省予算原本の公式入口・成立確認', url, '当初予算／一般会計各目明細書欄', excerpt))
        by_url[url]['source_ids'] = [sid]
    report = dict(schema_version=1, checked_at=CHECKED_AT, sources=sources, originals=originals, rows=rows,
                  verification_summary=dict(comparison_rows=86, university_corporations=81, inter_university_corporations=4,
                                            unallocated_pool_rows=1, initial_appropriation_thousand_yen=TOTALS,
                                            appendix_checked_entity_values=170),
                  research_notes=[dict(ministry='文部科学省', status='法人別当初予算積算内訳を原本照合・地域帰属未確認',
                                       note='81国立大学法人・4大学共同利用機関法人と未法人配分枠の両年度86区分を確認。交付決定額・決算と区別。法人名・本部所在地で県に割り当てない。',
                                       source_ids=[f'mext-university-initial-{y}' for y in (2025, 2026)]),
                                  dict(ministry='文部科学省', status='成果指標説明は法人別金額比較に不採用',
                                       note='成果に基づく配分資料は運営費交付金のうち1,000億円の配分指標・算式の説明であり、85法人の交付金総額表ではない。指標値や配分率から金額を推定しない。URL内の年月と資料の対象年度が異なるためPDF本文と公式入口で年度を確定。',
                                       source_ids=[f'mext-university-performance-{y}' for y in (2025, 2026)])],
                  limitations=['国立大学法人運営費交付金の積算内訳のみ。文科省の他制度・地域配分を網羅しない。',
                               '一般会計所管総額に含まれる内訳のため、所管総額や成果配分対象経費1,000億円と足し合わせない。',
                               '県別配分・キャンパス別配分・交付決定額・2026年度決算は未確認。'])
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print('PASS: 172 initial-budget breakdown values; 170 entity values cross-checked with annual-plan appendix; 86 rows; 9 original receipts')


if __name__ == '__main__':
    main()
