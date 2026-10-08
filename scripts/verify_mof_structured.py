"""Independently reconcile MOF expenditure CSV originals with the public snapshot.

Run without arguments to verify cached, hash-checked originals. --fetch downloads
the official files with urllib's inherited proxy and TLS verification enabled.
Raw CSV/ZIP/XML bytes remain in the ignored .cache directory; the generated
report contains only source receipts and reviewed ministry/account totals.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import urllib.request
import zipfile
from fetch_sources import CheckedRedirect, MAX_BYTES, checked

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/originals'
MANIFEST = ROOT / '.cache/mof-structured-manifest.json'
OUTPUT = ROOT / 'data/mof-structured-verification.json'
CHECKED_AT = '2026-10-09'


def specifications():
    for year in (2025, 2026):
        base = f'https://www.bb.mof.go.jp/server/{year}/'
        for category, number in [('general', '11'), ('special', '12')]:
            yield base + f'csv/DL{year}{number}001.zip'
        yield base + f'html/{year}11001Main.html'
        yield base + f'html/{year}11001menu.html'
        yield base + f'xml/{year}11001910001a.xml'
        page = '041' if year == 2025 else '042'
        yield base + f'xml/{year}11001000{page}a.xml'


def fetch():
    CACHE.mkdir(parents=True, exist_ok=True)
    originals = []
    for url in specifications():
        with urllib.request.build_opener(CheckedRedirect()).open(checked(url), timeout=25) as response:
            content = response.read(MAX_BYTES + 1)
            if len(content) > MAX_BYTES:
                raise ValueError('File exceeds size limit')
            final_url = checked(response.url)
            content_type = response.headers.get('Content-Type')
        relative = Path('.cache/originals') / (hashlib.sha256(url.encode()).hexdigest() + '.bin')
        (ROOT / relative).write_bytes(content)
        originals.append(dict(url=url, final_url=final_url,
                              retrieved_at_utc=datetime.now(timezone.utc).isoformat(),
                              sha256_original=hashlib.sha256(content).hexdigest(),
                              bytes=len(content), content_type=content_type,
                              path=str(relative)))
        # Preserve receipts even if a subsequent download fails.
        MANIFEST.write_text(json.dumps({'originals': originals}, ensure_ascii=False, indent=2) + '\n')


def load_original(receipt):
    content = (ROOT / receipt['path']).read_bytes()
    assert len(content) == receipt['bytes'], receipt['url']
    assert hashlib.sha256(content).hexdigest() == receipt['sha256_original'], receipt['url']
    return content


def reconcile(original, snapshot):
    year = int(original['url'].split('/')[4])
    special = f'DL{year}12' in original['url']
    category = 'special' if special else 'general'
    source_id = f'mof-csv-{category}-{year}'
    # a.csv contains revenue; b.csv contains expenditure only, at leaf 目 level.
    member = f'DL{year}{"12" if special else "11"}001b.csv'
    with zipfile.ZipFile(io.BytesIO(load_original(original))) as archive:
        if archive.getinfo(member).file_size > MAX_BYTES:
            raise ValueError('CSV exceeds size limit')
        content = archive.read(member)
    reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig')))
    field = f'令和{year - 2018}年度予定額(千円)' if special else f'令和{year - 2018}年度要求額(千円)'
    assert field in reader.fieldnames, member
    records = list(reader)
    assert records, member
    # Full field rows must be distinct. Same 目名 can recur across classification
    # codes and is not, by itself, evidence of a duplicate.
    assert len({tuple(row.values()) for row in records}) == len(records), member
    totals, counts = Counter(), Counter()
    for record in records:
        assert record['項コード'] and record['目名'], member
        group = (record['特別会計'], record['勘定']) if special else record['所管']
        totals[group] += int(record[field])
        counts[group] += 1
    comparisons = []
    represented_groups = set()
    for row in snapshot:
        if row['basis'] != '当初予算' or (row['account'] == '特別会計') != special:
            continue
        if special:
            account, subaccount = row['id'].removeprefix('special-').rsplit('-', 1)
            group = (account, '' if subaccount == '総額' else subaccount)
        else:
            group = row['ministry']
        # 2025 has 18 independent appropriating ministries. The new 防災庁
        # has no separate appropriation in the original, which is explicitly
        # distinguished from an observed zero-value appropriation.
        absence = not special and year == 2025 and group == '防災庁'
        if not absence:
            assert group in totals, (source_id, group)
            represented_groups.add(group)
        else:
            assert group not in totals
        expected = Decimal(str(row[f'amount{year}'])) * 1000
        assert expected == expected.to_integral_value(), row['id']
        actual = totals[group] if not absence else 0
        assert actual == int(expected), (source_id, row['id'], actual, expected)
        comparisons.append(dict(row_id=row['id'], field=f'amount{year}',
                                ministry=None if special else group,
                                account=group[0] if special else '一般会計',
                                subaccount=group[1] if special else None,
                                amount_thousand_yen=actual,
                                leaf_records=counts[group],
                                status='no_separate_appropriation' if absence else 'matched'))
    assert represented_groups == set(totals), (source_id, 'unrepresented source groups')
    assert len(comparisons) == (34 if special else 19), source_id
    method = ('ZIPの歳出b.csvのみをUTF-8で読み、目別明細の当該年度欄を整数千円で合計。'
              + ('特別会計・勘定で集計（勘定空欄は会計総額）。会計間の重複を控除しない歳出総額。' if special
                 else '所管で集計。2025防災庁の0は独立所管の項目不存在を表し、防災施策全体の支出ゼロを示さない。')
              + '歳入a.csv、前年度欄、比較欄を合計に使わず、目名だけで重複排除しない。'
                '総計・所管計・項計など上位集計行はCSVに含まれないため加算しない。')
    original['source_ids'] = [source_id]
    original['verification'] = dict(status='matched', checked_at=CHECKED_AT,
                                    method=method, locator=f'{member} / {field}',
                                    row_ids=[c['row_id'] for c in comparisons],
                                    fields=[f'amount{year}'])
    table = ['|所管／会計・勘定|歳出（千円）|目別明細数|', '|---|---:|---:|']
    for group, amount in totals.items():
        label = '・'.join(x for x in group if x) if special else group
        table.append(f'|{label}|{amount:,}|{counts[group]}|')
    if year == 2025 and not special:
        table.append('|防災庁（独立所管の項目なし）|0（表示上）|0|')
    table.append(f'|計（会計間重複未控除）|{sum(totals.values()):,}|{len(records)}|')
    excerpt = '\n'.join(table)
    source = dict(id=source_id,
                  title=f'{year}年度{ "特別" if special else "一般" }会計当初予算・歳出CSV' + ('（修正成立）' if year == 2025 and not special else ''),
                  url=original['url'], locator=f'{member}、{field}。{method}',
                  kind='予算・議員資料', accessed=CHECKED_AT, published=None,
                  retrieved_via='財務省公式CSV ZIP原本を直接取得し目別明細から独立集計',
                  sha256_extracted_text=hashlib.sha256(excerpt.encode()).hexdigest(),
                  excerpt=excerpt)
    return source, dict(source_id=source_id, year=year, account_category=category,
                        member=member, value_column=field, original_group_count=len(totals),
                        source_record_count=len(records), expenditure_thousand_yen=sum(totals.values()),
                        comparisons=comparisons)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true', help='Download official originals before verification')
    args = parser.parse_args()
    if args.fetch:
        fetch()
    originals = json.loads(MANIFEST.read_text())['originals']
    assert set(specifications()) == {r['url'] for r in originals}, 'Incomplete originals manifest'
    snapshot = json.loads((ROOT / 'public/data.json').read_text())['rows']
    report = dict(schema_version=1, checked_at=CHECKED_AT, sources=[], originals=[], results=[],
                  notes=['2025原本はteishutsu（政府提出案）ディレクトリではなく成立予算。一般会計XML表紙は「(修正成立)」と明記。',
                         'CSV一般会計歳出の欄名「要求額」は配布原本の表記。金額は成立した当初予算の所管総額に一致。',
                         'XMLは表紙と甲号歳出実体を取得。XML金額欄の自動集計照合は未実施。',
                         '特別会計の歳出は会計間の資金移転を含む総額であり、一般会計と足し合わせて国の純支出にはできない。'])
    for original in originals:
        load_original(original)
        if original['url'].endswith('.zip'):
            source, result = reconcile(original, snapshot)
            report['sources'].append(source)
            report['results'].append(result)
        else:
            original['source_ids'] = []
            original['verification'] = dict(status='downloaded_only', checked_at=CHECKED_AT,
                                            method='公式HTML入口・目次／XML表紙・甲号歳出実体を取得しハッシュ検証。金額の自動照合は未実施。',
                                            locator=original['url'].rsplit('/', 1)[1], row_ids=[], fields=[])
        report['originals'].append(original)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(f'PASS: {len(report["sources"])} CSV originals, 106 values, 12 hash-checked receipts -> {OUTPUT.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
