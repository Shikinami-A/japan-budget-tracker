"""Review ENV April notifications. Requires Poppler pdftotext; no Python dependencies."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET
from urllib.request import Request, build_opener

from fetch_sources import CheckedRedirect, MAX_BYTES, checked

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache' / 'originals'
DATE = '2026-10-09'
INDEX = 'https://www.env.go.jp/recycle/waste/3r_network/3_naiji.html'
URLS = {2025: 'https://www.env.go.jp/recycle/waste/3r_network/3_naiji/r070401.pdf',
        2026: 'https://www.env.go.jp/recycle/waste/3r_network/3_naiji/r080407.pdf'}
NS = {'x': 'http://www.w3.org/1999/xhtml'}


def sha(content):
    return hashlib.sha256(content).hexdigest()


def original(url, fetch):
    checked(url)
    path = CACHE / (sha(url.encode()) + '.bin')
    manifest = CACHE / 'env-manifest.jsonl'
    receipts = [json.loads(s) for s in manifest.read_text().splitlines()] if manifest.exists() else []
    existing = [r for r in receipts if r['url'] == url]
    if path.exists() and existing:
        receipt = existing[-1]
        if sha(path.read_bytes()) != receipt['sha256_original']:
            raise ValueError('Original hash mismatch')
        return path, receipt
    if not fetch:
        raise FileNotFoundError('Use --fetch to retrieve missing official original: ' + url)
    req = Request(url, headers={'User-Agent': 'japan-budget-tracker/0.1 (public official budget research)'})
    with build_opener(CheckedRedirect()).open(req, timeout=30) as response:
        content = response.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError('Original exceeds size limit')
        receipt = dict(url=url, final_url=checked(response.geturl()),
                       retrieved_at_utc=datetime.now(timezone.utc).isoformat(), bytes=len(content),
                       sha256_original=sha(content), content_type=response.headers.get_content_type())
    path.write_bytes(content)
    with manifest.open('a') as stream:
        stream.write(json.dumps(receipt, ensure_ascii=False) + '\n')
    return path, receipt


def words(page):
    return [dict(x0=float(w.attrib['xMin']), y0=float(w.attrib['yMin']),
                 x1=float(w.attrib['xMax']), y1=float(w.attrib['yMax']), text=w.text)
            for w in page.findall('.//x:word', NS)]


def center(w):
    return (w['y0'] + w['y1']) / 2


def extract(path, year, prefectures):
    bbox = subprocess.check_output(['pdftotext', '-bbox-layout', str(path), '-'], text=True)
    document = ET.fromstring(bbox)
    pages = document.findall('.//x:page', NS)
    cells = []
    facilities = []
    clipped = []
    amount_pattern = re.compile(r'(?:0|[1-9][0-9]{0,2}(?:,[0-9]{3})+|[1-9][0-9]*)')
    for page_number, page in enumerate(pages, 1):
        ws = words(page)
        if page_number == 1:
            continue
        if not any(w['text'] == '（単位：千円）' for w in ws):
            raise ValueError('Unrecognized page unit')
        if not any(w['text'] == ('内示額' if year == 2026 else '今回内示額') for w in ws):
            raise ValueError('Unrecognized amount header')
        pref_x = 188 if year == 2025 else 96
        pref_rows = sorted([w for w in ws if w['text'] in prefectures and
                            abs((w['x0'] + w['x1']) / 2 - pref_x) < 15], key=center)
        if not pref_rows:
            raise ValueError('Table page has no prefecture row')

        def assignment(cell):
            row_index = min(range(len(pref_rows)), key=lambda i: abs(center(pref_rows[i]) - center(cell)))
            row = pref_rows[row_index]
            low = (center(pref_rows[row_index - 1]) + center(row)) / 2 if row_index else center(row) - 13
            high = (center(pref_rows[row_index + 1]) + center(row)) / 2 if row_index + 1 < len(pref_rows) else center(row) + 13
            subject_x = 245.16 if year == 2025 else 178.30
            subject_words = sorted([w for w in ws if abs(w['x0'] - subject_x) < 1 and
                                    low <= center(w) < high], key=lambda w: (w['y0'], w['x0']))
            subject = ''.join(w['text'] for w in subject_words)
            # A few overflowing cells touch the following facility label in PDF text.
            for facility in ['マテリアルリサイクル推進施設', 'エネルギー回収型廃棄物処理施設']:
                subject = subject.split(facility)[0]
            if not subject:
                raise ValueError(f'Missing project owner on PDF page {page_number}')
            return row['text'], subject

        # Rightmost amounts are recipient totals, never add them to facility amounts.
        right = [w for w in ws if w['x0'] > 750 and amount_pattern.fullmatch(w['text'])]
        clusters = []
        for w in sorted(right, key=lambda w: (center(w), w['x0'])):
            if clusters and abs(center(w) - center(clusters[-1][0])) < 1:
                clusters[-1].append(w)
            else:
                clusters.append([w])
        for cluster in clusters:
            if len(cluster) > 1:
                clipped.append(dict(pdf_page=page_number, fragments=[w['text'] for w in cluster],
                                    reason='金額の文字分割を検出。手動確認が必要。'))
                raise ValueError('Unexpected fragmented amount in Poppler text')
            w = cluster[0]
            pref, subject = assignment(w)
            cells.append(dict(prefecture=pref, recipient_extracted=subject,
                              amount_thousand_yen=int(w['text'].replace(',', '')), pdf_page=page_number,
                              column='事業主体別今回内示額' if year == 2025 else '内示額',
                              bbox=[round(w[k], 3) for k in ['x0', 'y0', 'x1', 'y1']]))
        if year == 2025:
            current = [w for w in ws if 690 < w['x1'] < 710 and amount_pattern.fullmatch(w['text'])]
            previous = [w for w in ws if 643 < w['x1'] < 665 and amount_pattern.fullmatch(w['text'])]
            total = [w for w in ws if 735 < w['x1'] < 752 and amount_pattern.fullmatch(w['text'])]
            if len(current) != len(previous) or len(current) != len(total):
                raise ValueError('Facility amount column cardinalities differ')
            if any(int(w['text'].replace(',', '')) for w in previous):
                raise ValueError('First notification has prior allocations; review fiscal scope')
            if sum(int(w['text'].replace(',', '')) for w in current) != sum(int(w['text'].replace(',', '')) for w in total):
                raise ValueError('Current versus total facility amounts differ')
            for w in current:
                pref, subject = assignment(w)
                facilities.append(dict(prefecture=pref, recipient_extracted=subject,
                                       amount_thousand_yen=int(w['text'].replace(',', '')), pdf_page=page_number))
    unique_cells = []
    for cell in cells:
        previous = unique_cells[-1] if unique_cells else None
        same_owner_amount = previous is not None and all(cell[k] == previous[k] for k in
                                                        ['prefecture', 'recipient_extracted', 'amount_thousand_yen'])
        if same_owner_amount and cell['pdf_page'] == previous['pdf_page'] + 1:
            if year != 2025:
                raise ValueError('Unexpected repeated merged cell in 2026')
            clipped.append(dict(pdf_page=cell['pdf_page'], preceding_pdf_page=previous['pdf_page'],
                                prefecture=cell['prefecture'], recipient_extracted=cell['recipient_extracted'],
                                amount_thousand_yen=cell['amount_thousand_yen'],
                                reason='改頁をまたぐ同一事業主体の結合セル再掲。1回のみ計上。'))
            continue
        unique_cells.append(cell)
    cells = unique_cells
    totals = Counter()
    for cell in cells:
        totals[cell['prefecture']] += cell['amount_thousand_yen']
    if set(totals) != set(prefectures):
        raise ValueError('Incomplete prefectural coverage')
    if year == 2025:
        facility_totals = Counter()
        for cell in facilities:
            facility_totals[cell['prefecture']] += cell['amount_thousand_yen']
        if totals != facility_totals:
            raise ValueError('Recipient totals versus independent facility sums differ: ' +
                             str({p: (totals[p], facility_totals[p]) for p in prefectures if totals[p] != facility_totals[p]}))
        if len(clipped) != 2:
            raise ValueError('Unexpected page continuation count; review PDF layout')
    elif clipped:
        raise ValueError('Unexpected clipped amount; review 2026 layout')
    return dict(totals=dict(totals), cells=cells, clipped_cells_excluded=clipped,
                facility_amount_count=len(facilities), pdf_pages=len(pages))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    prefectures = json.loads((ROOT / 'public' / 'data.json').read_text())['prefectures']
    sources = []
    originals = []
    extracted = {}
    index_path, index_receipt = original(INDEX, args.fetch)
    index_html = index_path.read_text()
    if not all(url in index_html for url in URLS.values()):
        raise ValueError('Notification URLs absent from official index')
    index_excerpt = re.search(r'<h4>令和８年度</h4>(.*?)<h4>令和６年度</h4>', index_html, re.S)[0]
    index_text = re.sub('<[^>]+>', ' ', index_excerpt)
    sources.append(dict(id='env-notification-index', title='環境省：循環型社会形成推進交付金 内示情報',
                        url=INDEX, locator='令和8年度4月、令和7年度4月・期中内示の一覧', kind='地域配分索引',
                        accessed=DATE, published=None, retrieved_via='公式HTML原本取得',
                        excerpt=index_text, sha256_extracted_text=sha(index_text.encode())))
    originals.append({**index_receipt, 'source_ids': ['env-notification-index'],
                      'verification': dict(status='downloaded_only', checked_at=DATE, method='年度見出しと4月PDFリンク確認',
                                           locator='令和7年度・令和8年度の内示一覧', row_ids=[], fields=[], values={})})
    note = ('2025年4月1日第1回と2026年4月7日の公表内示掲載額を比較する参考値。'
            '2025表は一般会計・エネルギー対策特別会計を含み、2026表は会計区分を掲載しない。'
            '交付金等の内示額であり、総事業費・執行額とは区別する。'
            '会計別・制度別の同条件比較、年度末追加内示、補正・繰越の影響、内示後変更・執行額は未照合。'
            '2025は事業主体別合計欄を1回だけ計上し、施設別今回内示額合計と47県すべて一致することを確認。'
            '施設別額と事業主体別合計を加算せず、都道府県の直接受取額とも扱わない。')
    for year, url in URLS.items():
        path, receipt = original(url, args.fetch)
        text = subprocess.check_output(['pdftotext', '-layout', str(path), '-'], text=True)
        expected_date = '令和7年4月1日' if year == 2025 else '令和8年4月7日'
        if expected_date not in text.split('\f')[0].replace(' ', ''):
            raise ValueError('Unexpected notification cover date')
        extracted[year] = extract(path, year, prefectures)
        sid = f'env-circular-april-{year}'
        excerpt = text.split('\f')[0] + '\n集計対象：千円、事業主体別内示額。以下の県別額は原表の各主体額を集計した値。\n' + '\n'.join(
            f'{p} {extracted[year]["totals"][p]:,}' for p in prefectures)
        locator = f'PDF2〜{extracted[year]["pdf_pages"]}頁、右端の事業主体別内示額（千円）'
        sources.append(dict(id=sid, title=f'{year}年度循環型社会形成推進交付金等 4月内示', url=url,
                            locator=locator, kind='予算・地域配分資料', accessed=DATE,
                            published='2025-04-01' if year == 2025 else '2026-04-07',
                            retrieved_via='公式PDF原本取得、pdftotext -bbox-layoutで結合セルを識別し集計',
                            sha256_extracted_text=sha(excerpt.encode()), excerpt=excerpt))
        values = {f'env-circular-april-{p}': {f'amount{year}': extracted[year]['totals'][p] / 1000} for p in prefectures}
        originals.append({**receipt, 'source_ids': [sid], 'verification': dict(
            status='matched', checked_at=DATE, method='PDF座標で右端内示額を識別し県別集計。2025は施設別列合計で独立検算。',
            locator=locator, row_ids=list(values), fields=[f'amount{year}'], values=values)})
    rows = [dict(id=f'env-circular-april-{p}', ministry='環境省',
                 program='循環型社会形成推進交付金等（4月内示掲載額合計）', region=p, prefecture=p,
                 basis='4月内示（会計未分離・参考比較）', account='一般会計・特別会計未分離', unit='百万円',
                 source_ids=['env-circular-april-2025', 'env-circular-april-2026'],
                 period2025='2025年度4月1日第1回内示', period2026='2026年度4月7日内示',
                 amount2025=extracted[2025]['totals'][p] / 1000, amount2026=extracted[2026]['totals'][p] / 1000,
                 amount_thousand_yen2025=extracted[2025]['totals'][p], amount_thousand_yen2026=extracted[2026]['totals'][p],
                 evidence_status='公式原本照合済み', comparability='会計区分未分離・参考比較',
                 scope='県内事業主体の公表内示掲載額。県への直接交付額ではない。',
                 precision='原表の千円を百万円に換算（小数3桁）', note=note) for p in prefectures]
    output = dict(schema_version=1, checked_at=DATE, sources=sources, originals=originals, rows=rows,
                  extracted=extracted,
                  verification_summary=dict(prefectures=47, numeric_values=94,
                      totals_thousand_yen={y: sum(x['totals'].values()) for y, x in extracted.items()},
                      method='事業主体別合計のみ採用。FY2025施設別列との47県独立一致・既内示額全0。'),
                  research_notes=[dict(ministry='環境省', status='47県4月内示を原本照合・会計未分離の参考比較',
                                       note=note, source_ids=[s['id'] for s in sources])], limitations=[note])
    (ROOT / 'data' / 'reviewed-environment.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(output['verification_summary'], ensure_ascii=False))


if __name__ == '__main__':
    main()
