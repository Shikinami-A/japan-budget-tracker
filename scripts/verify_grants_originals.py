"""Independently compare cached official grant PDFs with public/data.json.

Requires Poppler pdftotext. Cache-only unless --fetch; ignores Exa text snapshots.
PDF coordinates select the actual table columns; missing rows remain null.
"""
import hashlib
import argparse
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import build_opener
from fetch_sources import CheckedRedirect, MAX_BYTES, checked

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/originals'
NS = {'x': 'http://www.w3.org/1999/xhtml'}
SOURCE_IDS = ('care-2025', 'care-2026', 'defense-2025', 'defense-2026')


def table_rows(pdf):
    xml = subprocess.check_output(['pdftotext', '-bbox-layout', str(pdf), '-'])
    root = ET.fromstring(xml)
    for page_number, page in enumerate(root.findall('.//x:page', NS), 1):
        words = sorted(page.findall('.//x:word', NS),
                       key=lambda w: (float(w.get('yMin')), float(w.get('xMin'))))
        grouped = []
        for word in words:
            y = float(word.get('yMin'))
            if not grouped or y - grouped[-1][0] > 1:
                grouped.append((y, []))
            grouped[-1][1].append(word)
        for y, row in grouped:
            yield page_number, float(page.get('width')), y, sorted(row, key=lambda w: float(w.get('xMin')))


def column(words, start, end):
    return ''.join(w.text or '' for w in words if start <= float(w.get('xMin')) < end)


def integer(value):
    # Never translate a dash, blank, or unavailable value into zero.
    if not re.fullmatch(r'[0-9]+(?:,[0-9]{3})*', value):
        raise ValueError(f'Not a printed numeric value: {value!r}')
    return int(value.replace(',', ''))


def extract(source_id, pdf, prefectures):
    result = {}
    prefecture = None
    for page, width, y, words in table_rows(pdf):
        if source_id.startswith('care'):
            region = column(words, 0, width * .24)
            if region not in prefectures:
                continue
            plans = integer(column(words, width * .24, width * .36))
            amount = integer(column(words, width * .36, width * .5))
            key = (region, region)
            record = {'printed_amount': amount, 'printed_unit': '千円',
                      'amount_million_yen': amount / 1000, 'plans': plans,
                      'column': '都道府県分・内示額' if source_id.endswith('2025') else '都道府県分・計画額（千円）'}
        else:
            printed_prefecture = column(words, 0, 150)
            if printed_prefecture in prefectures:
                prefecture = printed_prefecture
            city = column(words, 340, 465)
            value = column(words, 500, 550)
            if not city.endswith(('市', '町', '村')) or not re.fullmatch(r'[0-9]+', value):
                continue
            if prefecture is None:
                raise ValueError('Missing prefecture in PDF table')
            # PDF itself uses ケ in 2026 and ヶ in 2025; retain both spellings.
            key = (prefecture, city.replace('鎌ケ谷', '鎌ヶ谷'))
            record = {'printed_region': city, 'printed_amount': integer(value),
                      'printed_unit': '百万円', 'amount_million_yen': integer(value),
                      'column': '特定防衛施設関連市町村名・金額（単位未満四捨五入）'}
        if key in result:
            raise ValueError(f'Duplicate PDF table key: {key}')
        record.update(prefecture=key[0], region=key[1], page=page,
                      y_min_points=round(y, 3), row_text=' '.join(w.text or '' for w in words))
        result[key] = record
    expected = 47 if source_id.startswith('care') else (120 if source_id.endswith('2025') else 122)
    if len(result) != expected:
        raise ValueError(f'{source_id}: extracted {len(result)}, expected {expected}')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch',action='store_true',help='Fetch the four reviewed official originals')
    args=parser.parse_args()
    data = json.loads((ROOT / 'public/data.json').read_text())
    sources = {s['id']: s for s in data['sources']}
    if args.fetch:
        CACHE.mkdir(parents=True,exist_ok=True)
        for sid in SOURCE_IDS:
            url=sources[sid]['url']
            with build_opener(CheckedRedirect()).open(checked(url),timeout=25) as response:
                content=response.read(MAX_BYTES+1)
                if len(content)>MAX_BYTES:raise ValueError('File exceeds size limit')
                receipt=dict(source_id=sid,url=url,final_url=checked(response.geturl()),status='downloaded',
                             retrieved_at_utc=datetime.now(timezone.utc).isoformat(),bytes=len(content),
                             sha256_original=hashlib.sha256(content).hexdigest(),content_type=response.headers.get_content_type())
            (CACHE/(hashlib.sha256(url.encode()).hexdigest()+'.bin')).write_bytes(content)
            with (CACHE/'grants-manifest.jsonl').open('a') as stream:stream.write(json.dumps(receipt)+'\n')
    receipts = {}
    for line in (CACHE / 'grants-manifest.jsonl').read_text().splitlines():
        receipt = json.loads(line)
        if receipt['status'] == 'downloaded':
            receipts[receipt['source_id']] = receipt
    report = {'schema_version': 1, 'checked_at': '2026-10-09',
              'generated_at_utc': datetime.now(timezone.utc).isoformat(),
              'method': 'Poppler pdftotext -bbox-layout; PDF x/y coordinates select table columns; independent of Exa text',
              'download_status_is_separate_from_verification': True,
              'originals': [], 'checks': [], 'issues': [],
              'metadata_observations': [
                  {'source_id': 'care-2025', 'locator': 'PDF p.1 and p.2 footnote',
                   'finding': '2025-10-24 printed on press release; underlines identify changes from earlier notifications. This is an updated first-consultation notification, not an initial-budget allocation.'},
                  {'source_id': 'care-2026', 'locator': 'PDF p.1 column header and footnotes',
                   'finding': 'Amount column is 計画額（千円）; 471 plans / 4,741,743 thousand yen are a national-resilience subset across all municipalities. No publication date or explicit supplementary-budget statement appears in this PDF.'},
                  {'source_id': 'defense-2025', 'locator': 'PDF p.4 total and footnote',
                   'finding': '73 facilities / 120 municipalities / 11,993 million yen; per-municipality values rounded and need not sum to printed total. PDF title says 実施計画, not 当初.'},
                  {'source_id': 'defense-2026', 'locator': 'PDF p.4 total and footnote',
                   'finding': '76 facilities / 122 municipalities / 12,119 million yen; per-municipality values rounded and need not sum to printed total. PDF title says 実施計画, not 当初.'}
              ]}
    for source_id in SOURCE_IDS:
        source = sources[source_id]
        receipt = receipts[source_id]
        pdf = CACHE / (hashlib.sha256(source['url'].encode()).hexdigest() + '.bin')
        content = pdf.read_bytes()
        if hashlib.sha256(content).hexdigest() != receipt['sha256_original'] or len(content) != receipt['bytes']:
            raise ValueError(f'{source_id}: downloaded-original digest/size differs from receipt')
        extracted = extract(source_id, pdf, set(data['prefectures']))
        field = 'amount' + source_id.rsplit('-', 1)[1]
        family = source_id.split('-', 1)[0]
        public_rows = [r for r in data['rows'] if r['id'].startswith(family + '-')]
        row_ids = []
        issues = []
        public_keys = set()
        for row in public_rows:
            key = (row['prefecture'], row['region'])
            public_keys.add(key)
            original = extracted.get(key)
            expected = original['amount_million_yen'] if original else None
            checks = {'amount': row[field] == expected}
            if family == 'care':
                checks['plans'] = row['plans' + field[-4:]] == original['plans']
            matched = all(checks.values())
            check = {'source_id': source_id, 'row_id': row['id'], 'field': field,
                     'public_amount': row[field], 'original': original,
                     'status': 'matched' if matched else 'mismatch',
                     'checks': checks}
            if original is None:
                check['absence_status'] = 'not_listed_in_this_original; not a printed zero'
            report['checks'].append(check)
            if not matched:
                issues.append({'row_id': row['id'], 'field': field, 'expected': expected, 'actual': row[field]})
            else:
                row_ids.append(row['id'])
        for key in extracted.keys() - public_keys:
            issues.append({'unrepresented_original_row': list(key)})
        report['issues'].extend(issues)
        item = {k: receipt[k] for k in ('url', 'final_url', 'retrieved_at_utc', 'sha256_original', 'bytes', 'content_type')}
        item.update(source_id=source_id, download_status='downloaded',
                    original_row_count=len(extracted),
                    rounded_display_row_sum=sum(r['printed_amount'] for r in extracted.values()),
                    verification={'status': 'matched' if not issues else 'mismatch',
                                  'checked_at': '2026-10-09', 'method': report['method'],
                                  'locator': 'PDF p.2, prefecture columns' if source_id == 'care-2025' else
                                             ('PDF p.1, prefecture columns' if family == 'care' else 'PDF pp.1-4, prefecture / municipality / amount columns'),
                                  'row_ids': row_ids, 'fields': [field],
                                  'values': {c['row_id']: {field: c['original']['amount_million_yen'] if c['original'] else None}
                                             for c in report['checks'] if c['source_id'] == source_id},
                                  'checked_public_rows': len(public_rows),
                                  'printed_numeric_rows': len(extracted),
                                  'not_listed_rows': len(public_rows) - len(extracted)})
        report['originals'].append(item)
    (ROOT / 'data/grants-original-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'originals': len(report['originals']), 'checks': len(report['checks']), 'issues': report['issues']}, ensure_ascii=False))
    if report['issues']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
