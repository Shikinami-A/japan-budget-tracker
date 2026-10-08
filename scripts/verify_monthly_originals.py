"""Verify monthly expenditure and announcement data from cached official HTML.

Uses Python's HTML parser on official bytes, not the saved Exa extraction.
Cache-only unless --fetch. A hyphen remains unavailable rather than numeric zero.
"""
import hashlib
import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import build_opener
from fetch_sources import CheckedRedirect, MAX_BYTES, checked

from verify_grants_originals import extract

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/originals'


class OfficialHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None
        self.text = []
        self.dates = []
        self.links = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style'):
            self.skip += 1
        if tag == 'tr':
            self.row = []
        if tag in ('td', 'th'):
            self.cell = []
        if tag == 'time' and attrs.get('datetime'):
            self.dates.append(attrs['datetime'])
        if tag == 'a' and attrs.get('href'):
            self.links.append(attrs['href'])

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip -= 1
        if tag in ('td', 'th') and self.cell is not None:
            if self.row is not None:
                self.row.append(re.sub(r'\s+', '', ''.join(self.cell)))
            self.cell = None
        if tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if not self.skip:
            self.text.append(data)
            if self.cell is not None:
                self.cell.append(data)


def integer(value):
    if not re.fullmatch(r'[0-9]+(?:,[0-9]{3})*', value):
        raise ValueError(f'Not numeric: {value!r}')
    return int(value.replace(',', ''))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch',action='store_true',help='Fetch the seven reviewed official originals')
    args=parser.parse_args()
    data = json.loads((ROOT / 'public/data.json').read_text())
    sources = {s['id']: s for s in data['sources']}
    if args.fetch:
        CACHE.mkdir(parents=True,exist_ok=True)
        ids=[f'{category}-{year}' for year in (2025,2026) for category in
             ('mof-july','mof-july-announcement','care-announcement')]
        targets={sid:sources[sid]['url'] for sid in ids}
        targets['care-2026-announced-pdf']='https://www.mhlw.go.jp/content/12300000/001715725.pdf'
        for sid,url in targets.items():
            with build_opener(CheckedRedirect()).open(checked(url),timeout=25) as response:
                content=response.read(MAX_BYTES+1)
                if len(content)>MAX_BYTES:raise ValueError('File exceeds size limit')
                receipt=dict(source_id=sid,url=url,final_url=checked(response.geturl()),status='downloaded',
                             retrieved_at_utc=datetime.now(timezone.utc).isoformat(),bytes=len(content),
                             sha256_original=hashlib.sha256(content).hexdigest(),content_type=response.headers.get_content_type())
            (CACHE/(hashlib.sha256(url.encode()).hexdigest()+'.bin')).write_bytes(content)
            with (CACHE/'monthly-manifest.jsonl').open('a') as stream:stream.write(json.dumps(receipt)+'\n')
    receipts = {}
    for line in (CACHE / 'monthly-manifest.jsonl').read_text().splitlines():
        r = json.loads(line)
        if r['status'] == 'downloaded':
            receipts[r['source_id']] = r
    report = {'schema_version': 1, 'checked_at': '2026-10-09',
              'generated_at_utc': datetime.now(timezone.utc).isoformat(),
              'download_status_is_separate_from_verification': True,
              'originals': [], 'checks': [], 'issues': [], 'observations': []}

    def original(source_id):
        receipt = receipts[source_id]
        pdf = CACHE / (hashlib.sha256(receipt['url'].encode()).hexdigest() + '.bin')
        content = pdf.read_bytes()
        if len(content) != receipt['bytes'] or hashlib.sha256(content).hexdigest() != receipt['sha256_original']:
            raise ValueError(f'{source_id}: cached byte digest differs')
        item = {k: receipt[k] for k in ('url', 'final_url', 'retrieved_at_utc', 'sha256_original', 'bytes', 'content_type')}
        item.update(source_id=source_id, download_status='downloaded')
        report['originals'].append(item)
        return item, pdf, content

    for year, expected_date in ((2025, '2025-09-19'), (2026, '2026-09-18')):
        source_id = f'mof-july-{year}'
        item, _, content = original(source_id)
        parser = OfficialHTML()
        parser.feed(content.decode('utf-8'))
        if ['本月分', '前月までの累計', '計'] not in parser.rows:
            raise ValueError('Monthly expenditure header topology differs')
        numeric_rows = {}
        missing_rows = []
        for row in parser.rows:
            if len(row) != 7 or not re.fullmatch(r'[0-9,]+', row[1]):
                continue
            if row[4] == '-':
                missing_rows.append({'ministry': row[0], 'printed_value': '-', 'status': 'not_numeric; do not replace with zero'})
                continue
            numeric_rows[row[0]] = {'cells': row, 'printed_amount_thousand_yen': integer(row[4]),
                                    'amount_million_yen': integer(row[4]) / 1000,
                                    'previous_cumulative_thousand_yen': integer(row[3])}
        if len(numeric_rows) != 19:
            raise ValueError(f'{source_id}: {len(numeric_rows)} rows instead of 19 including total')
        field = f'amount{year}'
        public_rows = [r for r in data['rows'] if r['id'].startswith('july-')]
        if len(public_rows) != 18:
            raise ValueError(f'Expected 18 public July rows, found {len(public_rows)}')
        values = {}
        raw_rows = {}
        for line in (ROOT / f'data/source-text/monthly-{year}.txt').read_text().splitlines():
            cells = [v.strip() for v in line.split('|')[1:-1]]
            if len(cells) == 7 and cells[0] in numeric_rows:
                raw_rows[cells[0]] = cells
        for row in public_rows:
            original_row = numeric_rows[row['ministry']]
            value = original_row['amount_million_yen']
            checks = {'public_amount_matches': row[field] == value,
                      'saved_text_amount_matches': integer(raw_rows[row['ministry']][4]) == original_row['printed_amount_thousand_yen']}
            matched = all(checks.values())
            result = {'source_id': source_id, 'row_id': row['id'], 'field': field,
                      'public_amount': row[field], 'original': original_row,
                      'checks': checks, 'status': 'matched' if matched else 'mismatch'}
            report['checks'].append(result)
            if not matched:
                report['issues'].append(result)
            values[row['id']] = {field: value}
        expected_total = 42254458573 if year == 2025 else 45665915744
        if numeric_rows['合計']['printed_amount_thousand_yen'] != expected_total:
            raise ValueError('Total differs from extracted source total')
        item.update(missing_rows=missing_rows, printed_total_thousand_yen=expected_total,
                    verification={'status': 'matched' if not report['issues'] else 'mismatch',
                                  'checked_at': '2026-10-09',
                                  'method': 'Python HTMLParser; preserve td cells and validate expenditure subheader 本月分 / 前月までの累計 / 計; select fifth cell (計)',
                                  'locator': '一般会計（2）歳出 table tbody, 支出済歳出額・計 column; printed unit 千円（千円未満切捨）',
                                  'row_ids': list(values), 'fields': [field], 'values': values})
        announcement_id = f'mof-july-announcement-{year}'
        announcement, _, content = original(announcement_id)
        parser = OfficialHTML()
        parser.feed(content.decode('utf-8'))
        text = ''.join(parser.text)
        era = year - 2018
        month, day = map(int, expected_date.split('-')[1:])
        printed_date = f'令和{era}年{month}月{day}日'
        statement = f'令和{era}年度の令和{era}年7月末における国庫歳入歳出状況'
        if printed_date not in text or statement not in text or sources[announcement_id]['published'] != expected_date:
            raise ValueError('Official announcement date or period differs')
        announcement.update(printed_date=printed_date, period_statement=statement,
                            verification={'status': 'matched', 'checked_at': '2026-10-09',
                                          'method': 'Official HTML text date and fiscal-year / July-end statement',
                                          'locator': '国庫歳入歳出状況 heading and right-aligned announcement-date paragraph',
                                          'row_ids': [], 'fields': ['published'], 'published': expected_date})

    for year, date in ((2025, '2025-10-24'), (2026, '2026-06-26')):
        source_id = f'care-announcement-{year}'
        item, _, content = original(source_id)
        parser = OfficialHTML()
        parser.feed(content.decode('utf-8'))
        if date not in parser.dates or sources[source_id]['published'] != date:
            raise ValueError('Care official publication date differs')
        pdf_links = [link for link in parser.links if link.endswith('.pdf') and link.startswith('/content/')]
        item.update(linked_pdf_urls=['https://www.mhlw.go.jp' + link for link in pdf_links],
                    verification={'status': 'matched', 'checked_at': '2026-10-09',
                                  'method': 'Official HTML time[datetime] attribute; date belongs to announcement page',
                                  'locator': 'm-boxInfo__date > time[datetime]', 'row_ids': [],
                                  'fields': ['published'], 'published': date})

    item, pdf, _ = original('care-2026-announced-pdf')
    existing_pdf = CACHE / (hashlib.sha256(sources['care-2026']['url'].encode()).hexdigest() + '.bin')
    existing_text = subprocess.check_output(['pdftotext', '-layout', str(existing_pdf), '-']).decode()
    announced_pages = subprocess.check_output(['pdftotext', '-layout', str(pdf), '-']).decode().split('\f')
    full_table_text_matches = ''.join(existing_text.split()) == ''.join(announced_pages[1].split())
    if not full_table_text_matches:
        report['issues'].append({'source_id': 'care-2026', 'issue': 'Full normalized table text differs between PDF versions'})
    table = extract('care-2026', pdf, set(data['prefectures']))
    values = {}
    for row in [r for r in data['rows'] if r['id'].startswith('care-')]:
        printed = table[(row['prefecture'], row['region'])]
        checks = {'amount': row['amount2026'] == printed['amount_million_yen'],
                  'plans': row['plans2026'] == printed['plans']}
        if not all(checks.values()):
            report['issues'].append({'row_id': row['id'], 'source_id': item['source_id'], 'checks': checks})
        values[row['id']] = {'amount2026': printed['amount_million_yen'], 'plans2026': printed['plans']}
    item.update(verification={'status': 'matched' if not report['issues'] else 'mismatch',
                              'checked_at': '2026-10-09',
                              'method': 'Poppler pdftotext -bbox-layout table coordinate extraction',
                              'locator': 'PDF p.2 prefecture columns, plan count and 計画額（千円）',
                              'row_ids': list(values), 'fields': ['amount2026'], 'values': values})
    report['observations'].append({
        'source_id': 'care-2026',
        'finding': '2026-06-26 announcement links /content/12300000/001715725.pdf, not existing /content/001715704.pdf. Linked PDF p.1 prints 令和８年６月26日 and 内示（１回目）; p.2 contains the same 47 prefecture amounts and plan counts as existing table-only PDF. This verifies announcement date and matching values, not the version/publication date of the other PDF URL.',
        'existing_pdf_publication_date_verified': False,
        'full_table_text_matches_ignoring_whitespace': full_table_text_matches,
        'announced_pdf_matching_prefectures': 47})
    (ROOT / 'data/monthly-original-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'originals': len(report['originals']), 'monthly_amount_checks': len(report['checks']),
                      'issues': report['issues']}, ensure_ascii=False))
    if report['issues']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
