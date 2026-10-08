"""Independently check saved MOF/Soumu PDFs using pdftotext, without network calls.

Run fetch_sources.py first. The reviewed receipts, not the binary originals,
are committed. Column positions and table labels are checked before values.
"""
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/originals'


def integer(text):
    return int(text.replace(',', ''))


def receipt_and_text(url):
    receipts = [json.loads(line) for line in (CACHE / 'manifest.jsonl').read_text().splitlines()]
    receipt = next(r for r in reversed(receipts) if r['url'] == url and r['status'] == 'downloaded')
    path = CACHE / (hashlib.sha256(url.encode()).hexdigest() + '.bin')
    content = path.read_bytes()
    assert len(content) == receipt['bytes']
    assert hashlib.sha256(content).hexdigest() == receipt['sha256_original']
    result = subprocess.run(['pdftotext', '-layout', str(path), '-'], check=True, capture_output=True)
    return receipt, result.stdout.decode('utf-8')


def match_amount(row, field, thousand_yen):
    assert round(row[field] * 1000) == thousand_yen, (row['id'], field, row[field], thousand_yen)


def main():
    data = json.loads((ROOT / 'public/data.json').read_text())
    sources = {s['id']: s for s in data['sources']}
    rows = data['rows']
    originals = []
    checked_at = datetime.now(timezone.utc).isoformat(timespec='seconds')

    def verified(receipt, source_ids, selected, locator, method, fields=None):
        original = dict(receipt)
        original['source_ids'] = source_ids
        original['verification'] = dict(status='matched', checked_at=checked_at,
            method=method, locator=locator, row_ids=[r['id'] for r in selected],
            fields=fields or ['amount2025', 'amount2026'],
            values={r['id']: {f: r[f] for f in (fields or ['amount2025', 'amount2026'])} for r in selected})
        originals.append(original)

    receipt, text = receipt_and_text(sources['mof-initial']['url'])
    pages = text.split('\f')
    table = next(p for p in pages if '一般会計歳出予算所管別対前年度比較表' in p)
    selected = [r for r in rows if r['region'] == '全国' and r['account'] == '一般会計'
                and r['basis'] in ('当初予算', '2025補正後対2026当初（非対称）')]
    names = {r['ministry'] for r in selected}
    values = {}
    for line in table.splitlines():
        compact = re.sub(r'\s+', '', line)
        match = re.match(r'([^\d]+)([\d,]+)(.*)', compact)
        if not match or match[1] not in names:
            continue
        first = re.search(r'\d', line).start()
        nums = re.findall(r'\d[\d,]*|―', line[first:])
        values[match[1]] = [0 if n == '―' else integer(n) for n in nums[:3]]
    assert set(values) == names and len(values) == 19
    for row in selected:
        b, initial, revised = values[row['ministry']]
        match_amount(row, 'amount2026', b)
        match_amount(row, 'amount2025', initial if row['basis'] == '当初予算' else revised)
    assert sum(v[0] for v in values.values()) == 122309247035
    assert sum(v[1] for v in values.values()) == 115197845248
    verified(receipt, ['mof-initial'], selected, '本文101頁（PDF107頁）、付表3',
             '原本PDFの所管名と先頭3列（2026当初・2025当初・2025補正後）を独立抽出。防災庁の前年欄は新設所管のダッシュ。')

    # Expenditure columns only. Parentheses are the previous initial budget;
    # the unparenthesized previous column is the supplemented budget.
    selected = [r for r in rows if r['account'] == '特別会計']
    extracted = []
    for page in pages[18:20]:
        assert '千円' in page
        lines = page.splitlines()
        for index, line in enumerate(lines):
            nums = list(re.finditer(r'\d[\d,]*', line))
            label = re.sub(r'\s+', '', line[:nums[0].start()]) if nums else ''
            if len(nums) != 6 or not label or '（' in label:
                continue
            b = integer(nums[1].group())
            a = integer(nums[3].group())
            preceding = lines[index - 1] if index else ''
            if '（' in preceding:
                old = [n for n in re.finditer(r'\d[\d,]*', preceding)
                       if abs(n.end() - nums[3].end()) <= 4]
                assert len(old) == 1, (label, preceding)
                a = integer(old[0].group())
            extracted.append((label, a, b))
    assert len(extracted) == len(selected) == 34
    for row, (label, a, b) in zip(selected, extracted):
        expected_label = row['scope'] if row['scope'] != '総額' else row['program'].removesuffix('特別会計')
        assert expected_label.startswith(label), (row['id'], label)
        match_amount(row, 'amount2025', a)
        match_amount(row, 'amount2026', b)
    verified(receipt, ['mof-special'], selected, '本文13〜14頁（PDF19〜20頁）、歳出列',
             '表の34会計・勘定ラベル、2026歳出列、2025歳出列の括弧内当初額を独立抽出。歳入と補正後額を除外。')

    receipt, text = receipt_and_text(sources['mof-q1']['url'])
    table = text.split('\f')[0]
    selected = [r for r in rows if r['basis'] == '執行額（4〜6月）']
    for row in selected:
        pattern = r'\s*'.join(map(re.escape, row['ministry'])) + r'\s+([\d,]+)\s+([\d,]+)'
        matches = re.findall(pattern, table)
        assert len(matches) == 1, row['id']
        b, a = map(integer, matches[0])
        match_amount(row, 'amount2025', a)
        match_amount(row, 'amount2026', b)
    assert len(selected) == 18
    assert re.search(r'防\s*災\s*庁\s+－\s+－', table)
    verified(receipt, ['mof-q1'], selected, 'PDF1頁、所管別内訳（千円未満切捨て）',
             '原本の所管名、2026第1四半期支出・前年同期列を独立抽出。防災庁のダッシュは未取得のまま。')

    receipt, text = receipt_and_text(sources['mof-annual-2025']['url'])
    compact = re.sub(r'\s+', '', text)
    assert '歳出決算総額（支出済歳出額）1,294,661' in compact
    assert data['annual2025']['spent_million_yen'] == 129466100
    verified(receipt, ['mof-annual-2025'], [], 'PDF2頁、歳出決算総額（億円未満切捨て）',
             '決算概要の支出済歳出額1,294,661億円を確認。', ['annual2025.spent_million_yen'])

    for year in (2025, 2026):
        sid = f'lat-{year}'
        receipt, text = receipt_and_text(sources[sid]['url'])
        table = text.split('都道府県別決定額（道府県分・市町村分）')[1]
        by_region = {}
        for line in table.splitlines():
            compact = re.sub(r'\s+', '', line)
            # Fixed separators are required: adjoining digits cannot delimit columns.
            parts = re.split(r'\s{2,}', line.strip())
            if len(parts) != 3:
                continue
            name = re.sub(r'\s+', '', parts[0])
            try:
                by_region[name] = [0 if n in ('-', '－') else integer(n) for n in parts[1:]]
            except ValueError:
                continue
        selected = [r for r in rows if r['program'] == '普通交付税']
        for row in selected:
            name = row['region'] if row['region'] == '北海道' else row['region'][:-1]
            actual = by_region[name][0 if row['scope'] == '道府県分' else 1]
            assert row[f'amount{year}'] == actual, (row['id'], year, actual)
        assert len(selected) == 94
        verified(receipt, [sid], selected, 'PDF最終頁、普通交付税都道府県別決定額（百万円）',
                 '47都道府県の道府県分・市町村分の各列を独立抽出。東京の道府県分は不交付団体のダッシュ。', [f'amount{year}'])

    output = dict(checked_at=checked_at, originals=originals,
                  note='取得済みと数値照合済みを分離。検索抽出本文の由来・ハッシュは変更しない。')
    (ROOT / 'data/core-original-verification.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
    print(f'Core originals verified: {len(originals)} source scopes, 184 comparison rows + annual total')


if __name__ == '__main__':
    main()
