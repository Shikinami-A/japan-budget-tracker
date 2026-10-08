"""Verify 2025 CAA strengthened-grant eligible expenses, not grant decisions.

Cache-only default; --fetch uses inherited proxy and verified TLS and continues
after individual retrieval failures. Offline validate_report needs no PDF tools.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fetch_sources import MAX_BYTES, failure_details
from verify_grants_originals import integer

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/other-ministry'
REPORT = ROOT / 'data/reviewed-caa-execution.json'
INDEX = 'https://www.caa.go.jp/policies/policy/local_cooperation/local_consumer_administration/grant/implementation_report_2025'
URLS = [INDEX + f'/assets/local_cooperation_cms203_260615_0{part}.pdf' for part in (2, 3, 4)]
DATE = '2026-10-09'
SECTION = r'５[.．]今年度都道府県及び市町村が実施した強化事業'
FIELDS = ['prefecture_project_expense_yen', 'prefecture_2024_supplementary_eligible_yen',
          'prefecture_2025_initial_eligible_yen', 'municipal_project_expense_yen',
          'municipal_2024_supplementary_eligible_yen', 'municipal_2025_initial_eligible_yen',
          'total_project_expense_yen', 'total_2024_supplementary_eligible_yen',
          'total_2025_initial_eligible_yen']


def sha(value):
    return hashlib.sha256(value).hexdigest()


def checked(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname != 'www.caa.go.jp' or
            parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError('Not an approved official CAA HTTPS resource')
    return url


class Redirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        depth = getattr(req, 'budget_redirects', 0)
        if depth >= 4:
            raise ValueError('Too many redirects')
        checked(newurl)
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if result is not None:
            result.budget_redirects = depth + 1
        return result


def canonical_receipt(url, observation):
    if not REPORT.exists():
        return observation
    reviewed = next((r for r in json.loads(REPORT.read_text())['originals'] if r['url'] == url), None)
    if reviewed is None:
        return observation
    if any(reviewed.get(k) != observation.get(k) for k in
           ('url', 'final_url', 'bytes', 'sha256_original', 'content_type')):
        raise ValueError('Changed original requires explicit review')
    return {**observation, 'retrieved_at_utc': reviewed['retrieved_at_utc']}


def original(url, fetch):
    checked(url)
    path = CACHE / (sha(url.encode()) + '.bin')
    manifest = CACHE / 'manifest.jsonl'
    receipts = [json.loads(line) for line in manifest.read_text().splitlines()] if manifest.exists() else []
    matching = [r for r in receipts if r['url'] == url and r.get('status') == 'downloaded']
    if path.exists() and matching:
        receipt = matching[-1]
        if sha(path.read_bytes()) != receipt['sha256_original'] or path.stat().st_size != receipt['bytes']:
            raise ValueError('Cached original digest/size differs from receipt')
        return path, canonical_receipt(url, receipt)
    if not fetch:
        return None, dict(url=url, status='not_cached', failure_category='original_not_collected')
    receipt = dict(url=url, retrieved_at_utc=datetime.now(timezone.utc).isoformat())
    try:
        request = Request(url, headers={'User-Agent': 'japan-budget-tracker/0.1 (public official budget research)'})
        with build_opener(Redirect()).open(request, timeout=25) as response:
            content = response.read(MAX_BYTES + 1)
            if len(content) > MAX_BYTES:
                raise ValueError('Original exceeds size limit')
            receipt.update(status='downloaded', final_url=checked(response.geturl()), bytes=len(content),
                           sha256_original=sha(content), content_type=response.headers.get_content_type())
        path.write_bytes(content)
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        receipt.update(status='blocked_or_failed', error_type=type(error).__name__, **failure_details(error))
        path = None
    with manifest.open('a') as stream:
        stream.write(json.dumps(receipt) + '\n')
    return path, canonical_receipt(url, receipt) if path is not None else receipt


def extract(path, prefectures):
    """Ruled cells versus independent Poppler layout, including all nine totals."""
    import pdfplumber
    layouts = subprocess.check_output(['pdftotext', '-layout', str(path), '-'], text=True).split('\f')
    records = []
    current = None
    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, 1):
            primary_text = re.sub(r'\s+', '', page.extract_text() or '')
            header = re.search(r'令和７年度(.+?)法人番号', primary_text)
            if header:
                current = header.group(1)
                if current not in prefectures:
                    raise ValueError('Unknown printed prefecture')
            if not re.search(SECTION, primary_text):
                continue
            if current is None or '令和７年度' not in primary_text or '本予算' not in primary_text or '（単位：円）' not in primary_text:
                raise ValueError('Missing year/unit/prefecture')
            header_rows = [row for table in page.extract_tables() for row in table
                           if len(row) == 11 and re.sub(r'\s+', '', row[3] or '') == '令和６年度補正予算']
            if len(header_rows) != 1 or any(re.sub(r'\s+', '', header_rows[0][k] or '') != '令和７年度本予算' for k in (4, 7, 10)):
                raise ValueError('Financial-year column headers changed')
            totals = [row for table in page.extract_tables() for row in table
                      if (row[0] or '').strip() == '合計']
            if len(totals) != 1 or len(totals[0]) != 11:
                raise ValueError('Unexpected final-total cells')
            values = [integer(cell) for cell in totals[0][2:]]
            independent_text = layouts[page_number - 1]
            if not re.search(SECTION, re.sub(r'\s+', '', independent_text)):
                raise ValueError('Independent extractor section mismatch')
            lines = [line for line in independent_text.splitlines() if re.match(r'^\s*合計\s', line)]
            if len(lines) != 1:
                raise ValueError('Independent extractor total cardinality mismatch')
            independent_values = [integer(v) for v in lines[0].split()[1:]]
            if values != independent_values:
                raise ValueError('Independent amount extraction mismatch')
            for offset in range(3):
                if values[offset] + values[offset + 3] != values[offset + 6]:
                    raise ValueError('Prefecture and municipal amounts differ from printed total')
            # Independently printed prefecture name on first report page.
            preceding = '\n'.join(layouts[:page_number])
            names = re.findall(r'令和７年度\s+(\S+)\s+法人番号', preceding)
            if not names or names[-1] != current:
                raise ValueError('Independent prefecture assignment mismatch')
            records.append(dict(prefecture=current, page=page_number, **dict(zip(FIELDS, values))))
    return records


def validate_report(report):
    """Offline arithmetic, evidence and saved-excerpt verification for CI."""
    records, rows = report['records'], report['rows']
    prefs = [r['prefecture'] for r in records]
    if len(prefs) != len(set(prefs)) or len(rows) != len(records):
        raise ValueError('Duplicate/inconsistent prefecture coverage')
    sources = {s['id']: s for s in report['sources']}
    original_by_url = {r['url']: r for r in report['originals']}
    original_urls = set(original_by_url)
    if INDEX in original_by_url:
        index_record = original_by_url[INDEX]
        index_proof = index_record['verification']
        if (index_record['source_ids'] or index_record['download_status'] != 'downloaded' or
                index_proof['status'] != 'downloaded_only' or index_proof['row_ids'] or
                index_proof['fields'] or index_proof['values']):
            raise ValueError('Index must not certify amounts')
    for source in sources.values():
        checked(source['url'])
        if source['url'] not in original_urls:
            raise ValueError('Source lacks original receipt')
        original_record = original_by_url[source['url']]
        associated = [r for r in rows if source['id'] in r['source_ids']]
        proof = original_record['verification']
        expected_values = {r['id']: {'amount2025': r['amount2025']} for r in associated}
        if (original_record['source_ids'] != [source['id']] or
                original_record['download_status'] != 'downloaded' or
                proof['status'] != 'matched' or proof['fields'] != ['amount2025'] or
                proof['row_ids'] != [r['id'] for r in associated] or proof['values'] != expected_values):
            raise ValueError('Original proof differs from associated reviewed 2025 amounts')
        excerpt = (ROOT / 'data/source-text' / (source['id'] + '.txt')).read_text()
        if sha(excerpt.encode()) != source['sha256_extracted_text'] or excerpt != source['excerpt']:
            raise ValueError('Saved source excerpt mismatch')
    for record, row in zip(records, rows):
        if any(type(record[k]) is not int or record[k] < 0 for k in FIELDS):
            raise ValueError('Invalid printed amount')
        for offset in range(3):
            if record[FIELDS[offset]] + record[FIELDS[offset + 3]] != record[FIELDS[offset + 6]]:
                raise ValueError('Broken recipient total')
        if (row['prefecture'] != record['prefecture'] or
                row['amount2025'] != record['total_2025_initial_eligible_yen'] / 1000000 or
                row['amount2026'] is not None or row['absence_status2026'] != 'not_collected' or
                row['comparability'] != '片年度未収載' or row['source_ids'] != [record['source_id']]):
            raise ValueError('Row differs from reviewed financial-year/stage scope')
        for k in FIELDS:
            if f'{k}={record[k]:,}' not in sources[record['source_id']]['excerpt']:
                raise ValueError('Printed value missing from public evidence')
    if report['reconciliation']['prefectures'] != len(records):
        raise ValueError('Summary count mismatch')
    if (report['reconciliation']['totals_yen'] != {k: sum(r[k] for r in records) for k in FIELDS} or
            report['reconciliation']['independently_verified_printed_amounts'] != len(records) * 9 or
            not report['reconciliation']['independent_poppler_equal'] or
            not report['reconciliation']['prefecture_plus_municipal_equal']):
        raise ValueError('Summary differs from verified records')
    return True


def validate_scope_report(report):
    """Validate saved bounded findings without claiming monetary verification."""
    if report['rows']:
        raise ValueError('Scope-only findings must not create monetary rows')
    originals = {r['url']: r for r in report['originals']}
    ids = set()
    for source in report['sources']:
        parsed = urlsplit(source['url'])
        if (parsed.scheme != 'https' or parsed.hostname not in
                {'www.mext.go.jp', 'www.digital.go.jp', 'www.caa.go.jp'} or
                parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise ValueError('Unexpected official scope source')
        if source['url'] not in originals:
            raise ValueError('Scope source lacks retrieval receipt')
        original_record = originals[source['url']]
        proof = original_record['verification']
        if (source['id'] not in original_record['source_ids'] or
                original_record['download_status'] != 'downloaded' or
                proof['status'] != 'downloaded_only' or proof['row_ids'] or
                proof['fields'] or proof['values']):
            raise ValueError('Scope-only original must not certify monetary values')
        excerpt = (ROOT / 'data/source-text' / (source['id'] + '.txt')).read_text()
        if excerpt != source['excerpt'] or sha(excerpt.encode()) != source['sha256_extracted_text']:
            raise ValueError('Scope excerpt differs from review')
        if source['id'] in ids:
            raise ValueError('Duplicate scope source')
        ids.add(source['id'])
    if any(set(f['source_ids']) - ids for f in report['findings']):
        raise ValueError('Finding lacks reviewed source')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    prefectures = set(json.loads((ROOT / 'public/data.json').read_text())['prefectures'])
    attempts, available = [], {}
    for url in [INDEX] + URLS:
        path, receipt = original(url, args.fetch)
        attempts.append(receipt)
        if path is not None:
            available[url] = path, receipt
    originals, sources, records, rows = [], [], [], []
    for part, url in zip((2, 3, 4), URLS):
        if url not in available:
            continue
        path, receipt = available[url]
        extracted = extract(path, prefectures)
        expected = {2: 14, 3: 16, 4: 17}[part]
        if len(extracted) != expected:
            raise ValueError('Original regional coverage changed')
        sid = f'caa-strengthened-execution-2025-part{part}'
        excerpt = '令和７年度 地方消費者行政強化交付金事業実績 第５表。今年度都道府県及び市町村が実施した強化事業。単位：円。令和６年度補正予算と令和７年度本予算の交付金対象経費を分離。\n'
        for record in extracted:
            record['source_id'] = sid
            excerpt += f"{record['prefecture']} PDF{record['page']}頁 合計 " + ' '.join(f'{k}={record[k]:,}' for k in FIELDS) + '\n'
            rows.append(dict(id='caa-strengthened-execution-2025-' + record['prefecture'],
                             ministry='消費者庁', agency='消費者庁', program='地方消費者行政強化交付金',
                             region=record['prefecture'], prefecture=record['prefecture'],
                             basis='事業実績の交付金対象経費（参考）', account='2025年度本予算財源',
                             unit='百万円', scope='県及び管内市町村の強化事業・2025年度本予算交付金対象経費',
                             period2025='2025年度事業実績', period2026='同範囲の2026年度事業実績未収載',
                             amount2025=record['total_2025_initial_eligible_yen'] / 1000000,
                             amount2026=None, absence_status2025=None, absence_status2026='not_collected',
                             source_ids=[sid], evidence_status='公式原本集計済み', comparability='片年度未収載',
                             geography_status='原本の都道府県名を独立確認。県及び管内市町村の総額で市町村・選挙区への配分は未照合。',
                             note='2025年度の強化事業実績のうち2025年度本予算財源の交付金対象経費。国庫の交付決定額・国の支出済額・事業経費総額・消費者行政決算見込み額と区別する。2024年度補正財源、推進事業（復興特会を含む）、事業計画を加算しない。県と市町村内訳の親合計だけを表示し、内訳と重複集計しない。2026年度公式申請表は段階・財源・制度区分が異なるため比較に使わず、同範囲の年度実績は未収載/null。金額だけで政治的影響を認定しない。'))
        records.extend(extracted)
        sources.append(dict(id=sid, title='2025年度地方消費者行政強化交付金事業実績・第５表', url=url,
                            locator=f'PDF内各県第５表、{expected}県の合計行', kind='予算・議員資料', accessed=DATE,
                            retrieved_via='公式PDF罫線セルと独立Poppler配置テキストの9金額及び県名を照合',
                            excerpt=excerpt, sha256_extracted_text=sha(excerpt.encode())))
        associated = [row for row in rows if row['source_ids'] == [sid]]
        originals.append({**{k: receipt[k] for k in ('url', 'final_url', 'retrieved_at_utc',
                          'sha256_original', 'bytes', 'content_type')},
                          'source_ids': [sid], 'download_status': 'downloaded',
                          'verification': dict(status='matched', checked_at=DATE,
                            method=f'独立した罫線セル・Poppler配置本文で9列{expected*9}金額及び{expected}県名を照合。県と市町村の合計3列を検算。2025本予算の対象経費だけをamount2025へ変換。',
                            locator=sources[-1]['locator'], row_ids=[r['id'] for r in associated],
                            fields=['amount2025'], values={r['id']: {'amount2025': r['amount2025']} for r in associated})})
    if INDEX in available:
        receipt = available[INDEX][1]
        originals.append({**{k: receipt[k] for k in ('url', 'final_url', 'retrieved_at_utc',
                          'sha256_original', 'bytes', 'content_type')},
                          'source_ids': [], 'download_status': 'downloaded',
                          'verification': dict(status='downloaded_only', checked_at=DATE,
                            method='公式年度実績索引取得。金額検証対象はリンク先の各県第5表。',
                            locator='2025年度事業実績の3地域分割PDFリンク', row_ids=[], fields=[], values={})})
    report = dict(review_date=DATE, sources=sources, originals=originals, retrieval_attempts=attempts,
                  records=records, rows=rows, reconciliation=dict(prefectures=len(records),
                  independently_verified_printed_amounts=len(records)*9,
                  independent_poppler_equal=True, prefecture_plus_municipal_equal=True,
                  totals_yen={k: sum(r[k] for r in records) for k in FIELDS}))
    if args.write:
        for source in sources:
            (ROOT / 'data/source-text' / (source['id'] + '.txt')).write_text(source['excerpt'])
        validate_report(report)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    else:
        validate_report(report)
        if json.loads(REPORT.read_text()) != report:
            raise ValueError('Regenerated review differs from saved report')
    print(json.dumps(report['reconciliation'], ensure_ascii=False))


if __name__ == '__main__':
    main()
