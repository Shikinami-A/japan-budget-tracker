"""Review MOF special-account cumulative expenditure; cache-only unless --fetch.

Validate fiscal/month headings and the nine-column rowspan/colspan table, then
independently read raw td rows. Keep printed dashes unavailable. No net national
total is inferred from accounts that include inter-account transfers.
"""
import argparse
import hashlib
import json
import re
import unicodedata
from html import unescape
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError

from fetch_sources import failure_details
from verify_mlit_originals import ROOT, fetch, original_path, receipt_for

CHECKED_AT = '2026-10-09'
OUT = ROOT / 'data/reviewed-special-execution.json'
URLS = {f'special-execution-{year}-{month}-{kind}':
        f'https://www.mof.go.jp/policy/budget/report/revenue_and_expenditure/fy{year}/{year-2018:02}{month:02}{suffix}.html'
        for year in (2025, 2026) for month in (6, 7)
        for kind, suffix in [('table', 'c'), ('announcement', 'a')]}


def normalized(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))


def integer_or_null(text):
    if text in ('-', '－', '―', '—'):
        return None
    if not re.fullmatch(r'[0-9]+(?:,[0-9]{3})*', text):
        raise ValueError('Unexpected amount cell')
    return int(text.replace(',', ''))


class TableParser(HTMLParser):
    """Retain table boundaries and original cell spans before expanding them."""
    def __init__(self):
        super().__init__()
        self.tables = []
        self.table = self.row = self.cell = None
        self.heading = None
        self.heading_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'h1':
            self.heading = []
        elif tag == 'table':
            if self.table is not None:
                raise ValueError('Nested table unsupported')
            self.table = []
        elif tag == 'tr' and self.table is not None:
            self.row = []
        elif tag in ('td', 'th') and self.row is not None:
            self.cell = {'text': [], 'colspan': int(attrs.get('colspan', 1)),
                         'rowspan': int(attrs.get('rowspan', 1))}

    def handle_endtag(self, tag):
        if tag == 'h1' and self.heading is not None:
            self.heading_text.append(normalized(''.join(self.heading)))
            self.heading = None
        elif tag in ('td', 'th') and self.cell is not None:
            self.cell['text'] = normalized(''.join(self.cell['text']))
            self.row.append(self.cell)
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == 'table' and self.table is not None:
            self.tables.append(self.table)
            self.table = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell['text'].append(data)
        if self.heading is not None:
            self.heading.append(data)


def expand(rows):
    grid = {}
    for y, row in enumerate(rows):
        x = 0
        for cell in row:
            while (y, x) in grid:
                x += 1
            for dy in range(cell['rowspan']):
                for dx in range(cell['colspan']):
                    key = (y + dy, x + dx)
                    assert key not in grid, 'Overlapping HTML spans'
                    grid[key] = cell['text']
            x += cell['colspan']
        assert {col for line, col in grid if line == y} == set(range(9)), 'Not nine columns'
    assert max(y for y, _ in grid) == len(rows) - 1
    return [[grid[y, x] for x in range(9)] for y in range(len(rows))]


def extract_table(content, year, month):
    html = content.decode('utf-8')
    parser = TableParser()
    parser.feed(html)
    expected = f'国庫歳入歳出状況2特別会計(令和{year-2018}年度令和{year-2018}年{month}月分)'
    assert expected in parser.heading_text, 'Fiscal year/month heading differs'
    tables = [t for t in parser.tables if any(c['text'] == '会計名' for r in t for c in r)]
    assert len(tables) == 1
    grid = expand(tables[0])
    assert grid[0] == ['単位千円(千円未満切捨)'] * 9
    assert grid[1][4:7] == ['収納済歳入額又は支出済歳出額'] * 3
    assert grid[2][4:7] == ['本月分', '前月までの累計', '計']
    records = {}
    account = subaccount = None
    for index, cells in enumerate(grid[3:], 4):
        if cells[0] and cells[0] == cells[1] == cells[2] and not cells[3]:
            account, subaccount = cells[0], None
        elif cells[1] and cells[1] == cells[2] and not cells[3]:
            subaccount = cells[1]
        elif cells[2] == '歳出':
            assert account is not None
            key = (account, subaccount)
            assert key not in records
            records[key] = {'amount_thousand_yen': integer_or_null(cells[6]),
                            'printed_value': cells[6], 'expanded_cells': cells,
                            'html_table_row_1based': index,
                            'monthly_amount_thousand_yen': integer_or_null(cells[4]),
                            'previous_cumulative_thousand_yen': integer_or_null(cells[5])}
    assert len(records) == 34
    # Independent regex cell extraction does not expand or use span coordinates.
    body = re.findall(r'<tbody[^>]*>(.*?)</tbody>', html, re.S)
    assert len(body) == 1
    independent = {}
    account = subaccount = None
    for raw_row in re.findall(r'<tr[^>]*>(.*?)</tr>', body[0], re.S):
        raw_cells = re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', raw_row, re.S)
        cells = [normalized(unescape(re.sub(r'<[^>]+>', '', c))) for c in raw_cells]
        labels = [c for c in cells if c]
        if len(labels) == 1 and labels[0] not in ('歳入', '歳出'):
            if labels[0].endswith('勘定'):
                subaccount = labels[0]
            else:
                account, subaccount = labels[0], None
        if cells and cells[0] == '歳出':
            assert len(cells) == 7
            key = (account, subaccount)
            assert key not in independent
            independent[key] = integer_or_null(cells[4])
    assert independent == {k: r['amount_thousand_yen'] for k, r in records.items()}
    return records


def announcement(content, year, month):
    text = normalized(unescape(re.sub(r'<[^>]+>', '', content.decode('utf-8'))))
    era = year - 2018
    assert f'令和{era}年度の令和{era}年{month}月末における国庫歳入歳出状況' in text
    dates = set(re.findall(rf'令和{era}年(\d+)月(\d+)日', text))
    assert len(dates) == 1
    m, day = next(iter(dates))
    return f'{year}-{int(m):02}-{int(day):02}'


def source(sid, title, excerpt, locator, published=None):
    return dict(id=sid, title=title, url=URLS[sid], locator=locator, kind='公式執行資料',
                accessed=CHECKED_AT, published=published,
                retrieved_via='財務省公式HTML原本を既存プロキシ・TLS検証付きHTTPSで取得。表セルを独立した2方式で照合。',
                sha256_extracted_text=hashlib.sha256(excerpt.encode()).hexdigest(), excerpt=excerpt)


def validate(report):
    """Validate committed evidence without caches or network access."""
    assert report['schema_version'] == 1
    sources = {s['id']: s for s in report['sources']}
    rows = {r['id']: r for r in report['rows']}
    assert len(sources) == len(report['sources'])
    assert len(rows) == len(report['rows'])
    for s in sources.values():
        assert s['url'] == URLS[s['id']]
        assert hashlib.sha256(s['excerpt'].encode()).hexdigest() == s['sha256_extracted_text']
    covered = set()
    for original in report['originals']:
        assert original['url'] == URLS[original['source_id']]
        assert re.fullmatch(r'[0-9a-f]{64}', original['sha256_original'])
        assert original['bytes'] > 0
        verification = original.get('verification')
        if not verification or verification['status'] != 'matched':
            continue
        for rid, values in verification.get('values', {}).items():
            for field, value in values.items():
                assert rows[rid][field] == value
                covered.add((rid, field))
    for r in rows.values():
        assert r['account'] == '特別会計' and r['region'] == '全国' and r['prefecture'] is None
        assert r['view_group'] == 'execution'
        assert r['comparability'] != '同範囲'
        assert r['source_ids'] and all(sid in sources for sid in r['source_ids'])
        for year in (2025, 2026):
            value, exact = r[f'amount{year}'], r[f'amount_thousand_yen{year}']
            assert (value is None and exact is None) or (isinstance(exact, int) and exact >= 0 and value == exact / 1000)
            if value is not None:
                assert (r['id'], f'amount{year}') in covered
    for check in report['checks']:
        row = rows[check['row_id']]
        year = check['field'].removeprefix('amount')
        assert row[f'amount_thousand_yen{year}'] == check['amount_thousand_yen']
        assert check['independent_parsers_agree'] and check['status'] == 'matched'
    if not report['failures']:
        assert len(rows) == 68 and len(sources) == len(report['originals']) == 8
        assert len(report['checks']) == 136 and len(report['cross_period_checks']) == 68
    for check in report['cross_period_checks']:
        assert check['july_previous_thousand_yen'] == check['june_total_thousand_yen']
        difference = check['independent_truncation_difference_thousand_yen']
        assert difference is None or difference in (0, 1)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--fetch', action='store_true')
    cli.add_argument('--write', action='store_true')
    args = cli.parse_args()
    report = dict(schema_version=1, checked_at=CHECKED_AT, sources=[], originals=[], rows=[],
                  failures=[], issues=[], cross_period_checks=[], checks=[],
                  download_status_is_separate_from_verification=True,
                  note='特別会計の会計・勘定別累計支出済歳出額。勘定間・会計間移転を含むため全国純計・一般会計と合算しない。4〜7月には4〜6月を含む。')
    tables, available, dates = {}, {}, {}
    for sid, url in URLS.items():
        try:
            if args.fetch:
                fetch(url)
            receipt = receipt_for(url)
            content = original_path(url).read_bytes()
            available[sid] = dict(receipt, source_id=sid, download_status='downloaded')
            _, _, year, month, kind = sid.split('-')
            year, month = int(year), int(month)
            if kind == 'table':
                tables[year, month] = extract_table(content, year, month)
            else:
                dates[year, month] = announcement(content, year, month)
        except (HTTPError, URLError, TimeoutError, ValueError, AssertionError, FileNotFoundError, StopIteration) as error:
            report['failures'].append(dict(source_id=sid, url=url, error_type=type(error).__name__,
                                           **failure_details(error)))
    for month in (6, 7):
        keys = set().union(*(set(tables.get((year, month), {})) for year in (2025, 2026)))
        for account, subaccount in sorted(keys, key=lambda k: (k[0], k[1] or '')):
            key = (account, subaccount)
            rid = 'special-execution-' + str(month) + '-' + hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:16]
            row = dict(id=rid, ministry='特別会計（所管別未分解）',
                       program=account + '特別会計・支出済歳出額', region='全国', prefecture=None,
                       special_account_name=account, subaccount=subaccount,
                       basis='執行額（4〜6月）' if month == 6 else '執行額（4〜7月累計）',
                       account='特別会計', unit='百万円', view_group='execution',
                       scope=subaccount or '会計全体（勘定分割なし）',
                       precision='千円未満切捨の原表整数千円を百万円へ換算（小数第3位）',
                       comparability='参考・同名勘定（制度範囲未確認）', evidence_status='公式原本集計済み',
                       source_ids=[],
                       note='同じ年度内4月から当該月末までの累計支出済歳出額・計欄。歳入・本月分・歳出予算現額を混ぜない。前年同期・会計名と勘定名の完全一致で照合し、事業構成・制度範囲の連続性は未確認。移転・償還等を含む歳出総計のため各勘定や一般会計と加算して全国純計を作らない。7月末累計は6月末累計を含むため両期間を加算しない。所管別・地域別執行額ではない。')
            for year in (2025, 2026):
                sid = f'special-execution-{year}-{month}-table'
                table = tables.get((year, month))
                original = (table or {}).get(key)
                value = original['amount_thousand_yen'] if original else None
                row[f'amount{year}'] = value / 1000 if value is not None else None
                row[f'amount_thousand_yen{year}'] = value
                row[f'period{year}'] = f'{year}年度4〜{month}月末累計'
                row[f'amount_status{year}'] = ('原本数値照合済み' if value is not None else
                                               '原本ダッシュ（ゼロ認定なし）' if original else
                                               '原本表に非掲載' if table else '未収載・原本取得または照合未完了')
                if original:
                    row['source_ids'].append(sid)
                    report['checks'].append(dict(source_id=sid, row_id=rid, field=f'amount{year}',
                        amount_thousand_yen=value, original_table_row_1based=original['html_table_row_1based'],
                        printed_value=original['printed_value'], numeric_amount_verified=value is not None,
                        independent_parsers_agree=True, status='matched'))
                announcement_sid = f'special-execution-{year}-{month}-announcement'
                if (year, month) in dates:
                    row['source_ids'].append(announcement_sid)
            if any(row[f'amount{year}'] is None for year in (2025, 2026)):
                row['comparability'] = ('片年度原本ダッシュ（ゼロ認定なし）' if any(
                    row[f'amount_status{year}'] == '原本ダッシュ（ゼロ認定なし）' for year in (2025, 2026)) else
                    '片年度欠損（非掲載・未収載を区別）')
            report['rows'].append(row)
    for sid, item in available.items():
        _, _, year, month, kind = sid.split('-')
        year, month = int(year), int(month)
        if kind == 'table' and (year, month) in tables:
            table = tables[year, month]
            excerpt = f'{year}年度4〜{month}月末累計。単位千円（千円未満切捨）。支出済歳出額・計\n'
            excerpt += '|会計|勘定|累計支出済歳出額（千円）|\n|---|---|---:|\n'
            excerpt += '\n'.join(f'|{account}|{subaccount or "勘定分割なし"}|{r["printed_value"]}|' for (account, subaccount), r in table.items())
            locator = '特別会計HTML表：歳出行・支出済歳出額の計（展開後第7列）。原表の会計名・勘定名を完全一致。単位千円。'
            rows = [r for r in report['rows'] if sid in r['source_ids']]
            field = f'amount{year}'
            values = {r['id']: {field: r[field]} for r in rows}
            item['verification'] = dict(status='matched', checked_at=CHECKED_AT,
                method='HTMLParserによる9列rowspan/colspan展開と、独立したtbody/td直接抽出の整数一致。年度・月・単位・累計ヘッダーを検証。',
                locator=locator, row_ids=list(values), fields=[field], values=values)
            s = source(sid, f'{year}年度特別会計・{month}月末累計執行', excerpt, locator, dates.get((year, month)))
            s['published_verification'] = '同年度・同月末の国庫歳入歳出状況発表頁で公表日を確認。当該特別会計表には個別公表日の印字なし。'
            report['sources'].append(s)
        elif kind == 'announcement' and (year, month) in dates:
            date = dates[year, month]
            excerpt = f'{date}公表。令和{year-2018}年度の令和{year-2018}年{month}月末における国庫歳入歳出状況。'
            locator = '発表頁の年月日と当該年度・月末の期間説明。'
            item['verification'] = dict(status='matched', checked_at=CHECKED_AT,
                method='原本HTMLの公表日・年度・月末期間説明を確認。金額照合とは別。',
                locator=locator, row_ids=[], fields=['published'], published=date)
            report['sources'].append(source(sid, f'{year}年度{month}月末国庫歳入歳出状況・発表', excerpt, locator, date))
        report['originals'].append(item)
    for year in (2025, 2026):
        june, july = tables.get((year, 6)), tables.get((year, 7))
        if june is None or july is None:
            continue
        assert set(june) == set(july)
        for key, j in july.items():
            earlier = june[key]['amount_thousand_yen']
            previous = j['previous_cumulative_thousand_yen']
            value = j['amount_thousand_yen']
            assert previous == earlier, 'July previous cumulative differs from June total'
            if value is not None and earlier is not None:
                assert value >= earlier
            monthly = j['monthly_amount_thousand_yen']
            difference = value - previous - monthly if all(v is not None for v in (value, previous, monthly)) else None
            # Components and total independently truncate less than one thousand yen.
            if difference is not None:
                assert difference in (0, 1), 'Cumulative exceeds independent truncation tolerance'
            report['cross_period_checks'].append(dict(year=year, account=key[0], subaccount=key[1],
                june_total_thousand_yen=earlier, july_previous_thousand_yen=previous,
                july_total_thousand_yen=value, july_month_thousand_yen=monthly,
                independent_truncation_difference_thousand_yen=difference, status='matched'))
    validate(report)
    if args.write and report['rows']:
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    elif OUT.exists():
        expected = json.loads(OUT.read_text())
        assert report == expected, 'Committed reviewed special execution differs'
    print(json.dumps(dict(rows=len(report['rows']), sources=len(report['sources']),
                         originals=len(report['originals']), checks=len(report['checks']),
                         cross_period_checks=len(report['cross_period_checks']), failures=report['failures']), ensure_ascii=False))
    if report['failures']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
