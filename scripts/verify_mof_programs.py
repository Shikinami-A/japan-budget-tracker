"""Review every general-account item using hash-checked cached MOF originals.

This offline command reads the manifest produced by verify_mof_structured.py.
Only expenditure b.csv current-year leaf amounts are summed; matching names
identify review candidates, not independently verified programme continuity.
The ordinary dashboard build reads the committed JSON and needs no raw cache.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import csv
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import zipfile

from fetch_sources import MAX_BYTES
from verify_mof_structured import MANIFEST, load_original

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'data/reviewed-mof-programs.json'
CHECKED_AT = '2026-10-09'
EXPECTED_TOTALS = {2025: 115197845248, 2026: 122309247035}
KEY_FIELDS = ('所管', '組織', '項名')


def million_yen(thousand_yen):
    """Keep exact thousand-yen backing values and check JSON decimal roundtrip."""
    if thousand_yen is None:
        return None
    value = float(Decimal(thousand_yen) / 1000)
    assert Decimal(str(value)) * 1000 == thousand_yen
    return value


def read_year(year, original):
    member = f'DL{year}11001b.csv'
    with zipfile.ZipFile(io.BytesIO(load_original(original))) as archive:
        if archive.getinfo(member).file_size > MAX_BYTES:
            raise ValueError('CSV exceeds size limit')
        content = archive.read(member)
    reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig')))
    column = f'令和{year - 2018}年度要求額(千円)'
    assert set(KEY_FIELDS + ('項コード', '目名', column)) <= set(reader.fieldnames)
    records = list(reader)
    assert records and len({tuple(row.values()) for row in records}) == len(records)
    amounts, counts, codes = Counter(), Counter(), defaultdict(set)
    for record in records:
        key = tuple(record[field] for field in KEY_FIELDS)
        assert all(key) and record['項コード'] and record['目名']
        assert record[column].isdigit(), (year, key)
        amounts[key] += int(record[column])
        counts[key] += 1
        codes[key].add(record['項コード'])
    # The full-name key is unique at item-code level within each year's file.
    # Never use those codes to join different years: classifications can change.
    assert all(len(value) == 1 for value in codes.values()), 'Names merge distinct item codes'
    assert sum(amounts.values()) == EXPECTED_TOTALS[year]
    return dict(amounts=amounts, counts=counts, codes=codes,
                member=member, column=column, leaf_records=len(records))


def main():
    manifest = json.loads(MANIFEST.read_text())['originals']
    originals = {}
    tables = {}
    for year in (2025, 2026):
        url = f'https://www.bb.mof.go.jp/server/{year}/csv/DL{year}11001.zip'
        found = [receipt for receipt in manifest if receipt['url'] == url]
        assert len(found) == 1, (year, 'Missing or duplicate official receipt')
        originals[year] = deepcopy(found[0])
        tables[year] = read_year(year, originals[year])
    keys = sorted(set(tables[2025]['amounts']) | set(tables[2026]['amounts']))
    ministries = sorted({key[0] for key in keys})
    assert len(ministries) == 19
    snapshot = json.loads((ROOT / 'public/data.json').read_text())['rows']
    parents = {row['ministry']: row for row in snapshot
               if row['basis'] == '当初予算' and row['account'] == '一般会計'
               and row['region'] == '全国'}
    assert set(parents) == set(ministries)
    reconciliations = []
    for year in (2025, 2026):
        ministry_totals = Counter()
        for key, amount in tables[year]['amounts'].items():
            ministry_totals[key[0]] += amount
        assert len(ministry_totals) == (18 if year == 2025 else 19)
        for ministry in ministries:
            present = ministry in ministry_totals
            amount = ministry_totals[ministry] if present else None
            parent = parents[ministry]
            if present:
                assert Decimal(str(parent[f'amount{year}'])) * 1000 == amount
            else:
                assert year == 2025 and ministry == '防災庁'
                assert parent[f'amount{year}'] == 0
            reconciliations.append(dict(year=year, ministry=ministry,
                                        amount_thousand_yen=amount,
                                        parent_row_id=parent['id'],
                                        status='matched' if present else 'no_separate_appropriation'))
    rows = []
    shared_note = (
        '財務省一般会計歳出CSVの目別明細から、所管・組織・項名の完全一致キーで項単位に集計。'
        '項コード・目コードの年度一致は仮定せず、名称が同じでも制度の対象・内容・組織範囲の連続性は未確認。'
        '名称変更・所管移管・組織再編は別行のままとし、新設・廃止や増減の原因とは認定しない。'
        '当該年度欄のみを加算し、前年度欄・比較欄は使用しない。'
        '所管総額の内訳のため親の所管総額と加算しない。'
        '特別会計、補正予算、決算・執行額、地域配分を含まない。全国の予算項であり所在地や受取地域を示さない。'
        '2025年度は修正成立、2026年度は成立当初予算。')
    for key in keys:
        ministry, organization, item = key
        digest = hashlib.sha256(json.dumps(key, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()[:20]
        row = dict(id=f'mof-general-item-{digest}', ministry=ministry,
                   program=f'{item}（組織：{organization}）', organization=organization,
                   item_name=item, match_key=list(key), region='全国', prefecture=None,
                   basis='当初予算内訳（組織・項別）', account='一般会計', unit='百万円',
                   source_ids=['mof-csv-general-2025', 'mof-csv-general-2026'],
                   period2025='2025年度当初予算（修正成立）', period2026='2026年度当初予算',
                   evidence_status='公式原本集計済み', scope='所管・組織・項名別（名称一致）',
                   precision='整数千円を百万円へ換算（小数第3位）', note=shared_note)
        missing = []
        for year in (2025, 2026):
            amount = tables[year]['amounts'].get(key)
            row[f'amount{year}'] = million_yen(amount)
            row[f'amount_thousand_yen{year}'] = amount
            row[f'leaf_records{year}'] = tables[year]['counts'].get(key, 0)
            row[f'item_codes{year}'] = sorted(tables[year]['codes'].get(key, set()))
            if amount is None:
                missing.append(year)
        row['comparability'] = ('片年度非掲載' if missing else
                                 '参考・同名称一致（制度連続性未確認）')
        if missing:
            row['note'] += f' {missing[0]}年度原本に当該完全名称キーは非掲載。交付・予算ゼロとは認定せずnullで保持。'
        rows.append(row)
    assert len({row['id'] for row in rows}) == len(rows)
    for year in (2025, 2026):
        assert sum(row[f'amount_thousand_yen{year}'] or 0 for row in rows) == EXPECTED_TOTALS[year]
        original = originals[year]
        original['source_ids'] = [f'mof-csv-general-{year}']
        original['verification'] = dict(
            status='matched', checked_at=CHECKED_AT,
            method='原本ZIPの歳出b.csvをハッシュ検証後に読み、目別明細の当該年度欄のみ整数千円で集計。'
                   '所管・組織・項名の完全一致キーで集約し、全所管の既存総額および全国合計と再照合。'
                   '完全名称キー非掲載はnull。コードの年度一致、制度連続性、新設・廃止の推測は行わない。',
            locator=f'{tables[year]["member"]} / {tables[year]["column"]} / 所管・組織・項名',
            row_ids=[row['id'] for row in rows], fields=[f'amount{year}'],
            values={row['id']: {f'amount{year}': row[f'amount{year}']} for row in rows})
    both = sum(all(row[f'amount{year}'] is not None for year in (2025, 2026)) for row in rows)
    report = dict(schema_version=1, checked_at=CHECKED_AT, sources=[], rows=rows,
                  originals=[originals[year] for year in (2025, 2026)],
                  ministry_reconciliations=reconciliations,
                  summary=dict(ministry_count=len(ministries), item_union_count=len(rows),
                               both_years_count=both, only_2025_count=sum(row['amount2026'] is None for row in rows),
                               only_2026_count=sum(row['amount2025'] is None for row in rows),
                               years={str(year): dict(item_count=len(tables[year]['amounts']),
                                                    leaf_records=tables[year]['leaf_records'],
                                                    expenditure_thousand_yen=EXPECTED_TOTALS[year])
                                      for year in (2025, 2026)}),
                  notes=[shared_note, '既存の財務省一般会計CSVソースIDを再利用。出典の原本取得日時は更新しない。',
                         '2025防災庁は独立所管が非掲載。既存所管総額の表示上0と別に、項別の非掲載はnullで保持。',
                         '片年度非掲載の額は完全名称キーの欠落であり、全予算・事業の消滅を示すものではない。'])
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(f'PASS: 19 ministries, {len(rows)} items ({both} in both years); '
          'all 13,570 leaf records and both national totals reconciled; no network access')


if __name__ == '__main__':
    main()
