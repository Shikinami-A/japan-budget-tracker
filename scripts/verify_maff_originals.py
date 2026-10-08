"""Verify two MAFF grants against official original PDFs (cache-only by default)."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urljoin
from urllib.request import Request, build_opener

from fetch_sources import CheckedRedirect, MAX_BYTES, checked

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache' / 'originals'
PROGRAMS = ['農山漁村地域整備交付金', '美しい森林づくり基盤整備交付金']


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.href = None
        self.label = ''

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.href = dict(attrs).get('href')
            self.label = ''

    def handle_data(self, data):
        if self.href:
            self.label += data

    def handle_endtag(self, tag):
        if tag == 'a' and self.href:
            self.links.append((self.href, self.label.strip()))
            self.href = None


def sha(content):
    return hashlib.sha256(content).hexdigest()


def original(url, fetch):
    """Inherited proxy and verified TLS; official HTTPS redirects only."""
    checked(url)
    path = CACHE / (sha(url.encode()) + '.bin')
    manifest = CACHE / 'maff-manifest.jsonl'
    receipts = [json.loads(line) for line in manifest.read_text().splitlines()] if manifest.exists() else []
    existing = [r for r in receipts if r['url'] == url and r['status'] == 'downloaded']
    if path.exists() and existing:
        receipt = existing[-1]
        if sha(path.read_bytes()) != receipt['sha256_original']:
            raise ValueError('Cached original hash mismatch')
        return path, receipt
    if not fetch:
        raise FileNotFoundError('Original not cached: ' + url)
    request = Request(url, headers={'User-Agent': 'japan-budget-tracker/0.1 (public official budget research)'})
    with build_opener(CheckedRedirect()).open(request, timeout=30) as response:
        content = response.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError('File exceeds size limit')
        receipt = dict(url=url, final_url=checked(response.geturl()), status='downloaded',
                       retrieved_at_utc=datetime.now(timezone.utc).isoformat(),
                       sha256_original=sha(content), bytes=len(content),
                       content_type=response.headers.get_content_type())
    path.write_bytes(content)
    # A single append avoids shared-manifest replacement; this agent owns this file.
    with manifest.open('a') as stream:
        stream.write(json.dumps(receipt, ensure_ascii=False) + '\n')
    return path, receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true', help='Fetch missing official originals through inherited proxy')
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    data = json.loads((ROOT / 'public' / 'data.json').read_text())
    prefectures = data['prefectures']
    short = {p if p == '北海道' else p[:-1]: p for p in prefectures}
    tasks = []
    indexes = []
    for year, era in [(2025, 7), (2026, 8)]:
        url = f'https://www.maff.go.jp/j/budget/kasyo/{era}tousyo/index.html'
        path, receipt = original(url, args.fetch)
        html = path.read_text()
        title = re.search(r'<title>(.*?)</title>', html, re.S)[1]
        indexes.append(dict(year=year, title=title, **receipt))
        links = Links()
        links.feed(html)
        for href, label in links.links:
            name = label.split('(')[0].strip()
            if name in short and '.pdf' in href:
                tasks.append((year, short[name], urljoin(url, href)))
    if len(tasks) != 94:
        raise ValueError(f'Expected 47 prefectures per year; found {len(tasks)}')
    for year in [2025, 2026]:
        names = [p for y, p, _ in tasks if y == year]
        if len(set(names)) != 47 or set(names) != set(prefectures):
            raise ValueError(f'{year} official index has duplicate or missing prefectures')

    def verify(task):
        year, prefecture, url = task
        try:
            path, receipt = original(url, args.fetch)
            if not path.read_bytes().startswith(b'%PDF-'):
                raise ValueError('Not a PDF original')
            text = subprocess.check_output(['pdftotext', '-layout', str(path), '-'], text=True)
            pages = text.split('\f')
            tables = [(i + 1, page) for i, page in enumerate(pages)
                      if f'交付金(令和{str(year - 2018).translate(str.maketrans("0123456789", "０１２３４５６７８９"))}年度当初予算)' in page]
            amounts = {}
            for page_number, page in tables:
                for line in page.splitlines():
                    for program in PROGRAMS:
                        if program in line:
                            match = re.fullmatch(r'\s*\d+\s+' + re.escape(prefecture) + r'\s+' + re.escape(program) + r'\s+([\d,]+)\s*', line)
                            if match is None:
                                raise ValueError('Unexpected grant row layout: ' + line)
                            amounts[program] = dict(amount=int(match[1].replace(',', '')), unit='百万円',
                                                    pdf_page=page_number, row_text=line.strip())
            source_ids = [s['id'] for s in data['sources'] if s['url'] == url]
            row_ids = [r['id'] for r in data['rows'] if r['prefecture'] == prefecture and r['program'] in amounts]
            return {**receipt, 'year': year, 'published': '2025-04-01' if year == 2025 else '2026-04-07',
                        'prefecture': prefecture, 'amounts': amounts, 'source_ids': source_ids,
                        'status': 'verified' if amounts else 'grant_table_not_listed',
                        'verification': dict(status='matched' if amounts else 'table_not_listed',
                                          checked_at='2026-10-09', method='pdftotext -layout; explicit grant-table rows',
                                          locator='PDF ' + ', '.join(str(p) for p in sorted({a['pdf_page'] for a in amounts.values()})) + '頁、交付金表（国費、百万円）',
                                          row_ids=row_ids, fields=[f'amount{year}'])}
        except Exception as error:
            return dict(year=year, prefecture=prefecture, url=url, status='failed',
                        error_type=type(error).__name__, error=str(error))

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(verify, tasks))
    by_key = {(r['year'], r['prefecture'], p): value['amount']
              for r in results for p, value in r.get('amounts', {}).items()}
    comparisons = []
    for row in data['rows']:
        if not row['id'].startswith('maff-'):
            continue
        for year in [2025, 2026]:
            actual = by_key.get((year, row['prefecture'], row['program']))
            previous = row[f'amount{year}']
            comparisons.append(dict(row_id=row['id'], year=year, current_amount=previous,
                                    original_amount=actual,
                                    status='not_listed_in_grant_table' if previous is None and actual is None else
                                    'matches' if previous == actual else
                                    'missing_resolved' if previous is None and actual is not None else
                                    'mismatch' if previous is not None and actual is not None else 'unverified'))
    output = dict(verified_at_utc=datetime.now(timezone.utc).isoformat(),
                  method='Official index links; original PDF SHA-256; pdftotext -layout; explicit grant-table rows',
                  scope='当初配分予定国費、百万円に四捨五入。執行額ではない。県別PDFの交付金表への掲載のみを対象とし、未掲載はゼロとしない。',
                  indexes=indexes, originals=results, comparisons=comparisons)
    for result in results:
        if any(c['status'] == 'mismatch' and c['year'] == result['year'] and
               c['row_id'] in result.get('verification', {}).get('row_ids', []) for c in comparisons):
            result['verification']['status'] = 'mismatch'
    (ROOT / 'data' / 'maff-original-verification.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(originals=len(results), failed=sum(r['status'] == 'failed' for r in results),
                          verified_amounts=len(by_key),
                          comparison_statuses={s: sum(c['status'] == s for c in comparisons)
                                               for s in sorted({c['status'] for c in comparisons})}), ensure_ascii=False))
    if any(r['status'] == 'failed' for r in results) or any(c['status'] == 'mismatch' for c in comparisons):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
