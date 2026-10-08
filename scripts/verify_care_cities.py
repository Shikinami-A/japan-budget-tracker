"""Review MHLW city-level first-consultation tables with two PDF extractors.

Cache-only by default; --fetch downloads official originals with inherited proxy
and verified TLS. --write writes reviewed evidence after independent equality,
recipient cardinality, subtotal, plan-count and prefectural checks pass.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.request import Request, build_opener
import xml.etree.ElementTree as ET
import zipfile

from fetch_sources import CheckedRedirect, MAX_BYTES, checked
from verify_grants_originals import table_rows, column, integer

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/other-ministry'
DATE = '2026-10-09'
URLS = {2025: 'https://www.mhlw.go.jp/content/12300000/001585303.pdf',
        2026: 'https://www.mhlw.go.jp/content/12300000/001715725.pdf'}
CODE_URL = 'https://www.soumu.go.jp/main_content/000925835.xlsx'
NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def sha(value):
    return hashlib.sha256(value).hexdigest()


def canonical_receipt(url, observation):
    """Keep reviewed first retrieval evidence; later observations stay cached."""
    report_path = ROOT / 'data/reviewed-care-cities.json'
    if not report_path.exists():
        return observation
    reviewed = next((item for item in json.loads(report_path.read_text())['originals']
                     if item['url'] == url), None)
    if reviewed is None:
        return observation
    fields = ('url', 'final_url', 'bytes', 'sha256_original', 'content_type')
    if any(observation.get(field) != reviewed.get(field) for field in fields):
        raise ValueError('Original differs from reviewed receipt; review changed content/metadata before publishing')
    return {**observation, 'retrieved_at_utc': reviewed['retrieved_at_utc']}


def original(url, fetch):
    checked(url)
    path = CACHE / (sha(url.encode()) + '.bin')
    manifest = CACHE / 'manifest.jsonl'
    receipts = [json.loads(line) for line in manifest.read_text().splitlines()] if manifest.exists() else []
    matched = [r for r in receipts if r['url'] == url and r.get('status') == 'downloaded']
    if not path.exists() or not matched:
        if not fetch:
            raise FileNotFoundError('Use --fetch for missing original: ' + url)
        req = Request(url, headers={'User-Agent': 'japan-budget-tracker/0.1 (public official budget research)'})
        with build_opener(CheckedRedirect()).open(req, timeout=25) as response:
            content = response.read(MAX_BYTES + 1)
            if len(content) > MAX_BYTES:
                raise ValueError('Original exceeds size limit')
            receipt = dict(url=url, final_url=checked(response.geturl()), status='downloaded',
                           retrieved_at_utc=datetime.now(timezone.utc).isoformat(), bytes=len(content),
                           sha256_original=sha(content), content_type=response.headers.get_content_type())
        path.write_bytes(content)
        with manifest.open('a') as stream:
            stream.write(json.dumps(receipt) + '\n')
    else:
        receipt = matched[-1]
    if sha(path.read_bytes()) != receipt['sha256_original'] or path.stat().st_size != receipt['bytes']:
        raise ValueError('Original digest/size differs from download receipt')
    return path, canonical_receipt(url, receipt)


def municipality_codes(path):
    with zipfile.ZipFile(path) as archive:
        strings = [''.join(t.itertext()) for t in
                   ET.fromstring(archive.read('xl/sharedStrings.xml')).findall('m:si', NS)]
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        if workbook.find('m:sheets/m:sheet', NS).get('name') != 'R6.1.1現在の団体':
            raise ValueError('Unexpected municipal-code reference date')
        rows = []
        for row in ET.fromstring(archive.read('xl/worksheets/sheet1.xml')).findall('m:sheetData/m:row', NS):
            values = {}
            for cell in row.findall('m:c', NS):
                value = cell.find('m:v', NS)
                if value is not None:
                    values[re.sub(r'\d+', '', cell.get('r'))] = strings[int(value.text)] if cell.get('t') == 's' else value.text
            if values.get('C', '').endswith('市') and re.fullmatch(r'\d{6}', values.get('A', '')):
                rows.append(dict(code=values['A'], prefecture=values['B'], city=values['C'], xlsx_row=int(row.get('r'))))
    result = {}
    for row in rows:
        result.setdefault(row['city'], []).append(row)
    return result


def extract_bbox(path, year, prefs):
    records, subtotals, grand = {}, {}, None
    left_group = '都道府県'
    for page, width, y, words in table_rows(path):
        if page != 2:
            continue
        left_name = column(words, 0, width * .24)
        if left_name == '指定都市名':
            left_group = '指定都市'
        for group, name, plans, amount in [
            (left_group, left_name, column(words, width * .24, width * .36), column(words, width * .36, width * .5)),
            ('中核市', column(words, width * .5, width * .69), column(words, width * .69, width * .83), column(words, width * .83, width))]:
            if not re.fullmatch(r'\d+', plans) or not re.fullmatch(r'\d+(?:,\d{3})*', amount):
                continue
            values = dict(plans=integer(plans), amount_thousand_yen=integer(amount))
            if name == '小計':
                if group in subtotals:
                    raise ValueError('Duplicate subtotal')
                subtotals[group] = values
            elif name == '合計':
                grand = values
            elif name in prefs or name.endswith('市'):
                key = (group, name)
                if key in records:
                    raise ValueError('Duplicate recipient')
                records[key] = dict(**values, page=page, y_min_points=round(y, 3), group=group, name=name,
                                    column='今回計画額' if group == '指定都市' and year == 2025 else ('内示額' if year == 2025 else '計画額（千円）'))
    if Counter(g for g, _ in records) != Counter({'都道府県': 47, '指定都市': 20, '中核市': 62}):
        raise ValueError('Incomplete or unexpected recipient coverage')
    for group in ('都道府県', '指定都市', '中核市'):
        total = {field: sum(v[field] for (g, _), v in records.items() if g == group)
                 for field in ('plans', 'amount_thousand_yen')}
        if total != subtotals[group]:
            raise ValueError('Recipient sum differs from printed subtotal')
    if grand != {field: sum(v[field] for v in subtotals.values()) for field in ('plans', 'amount_thousand_yen')}:
        raise ValueError('Subtotals differ from printed grand total')
    return records, subtotals, grand


def extract_layout(path, year, prefs):
    """Independent line parser without bbox coordinates or bbox row assignments."""
    text = subprocess.check_output(['pdftotext', '-layout', str(path), '-'], text=True)
    table = text.split('\f')[1]
    result = {}
    pattern = re.compile(r'\s*([^\d]+?)\s+(\d+)\s+([0-9,]+)(?=\s|$)')
    for line in table.splitlines():
        # The left designated-city header shares a line with the right 呉市
        # record; remove only that nonnumeric heading before line parsing.
        remaining = re.sub(r'^\s*指定都市名\s+計画数\s+(?:今回計画額|計画額（千円）)', '', line)
        while (match := pattern.match(remaining)):
            name = re.sub(r'\s+', '', match[1])
            if name in prefs or name.endswith('市'):
                if name in result:
                    raise ValueError('Duplicate independently extracted recipient')
                result[name] = dict(plans=integer(match[2]), amount_thousand_yen=integer(match[3]))
            remaining = remaining[match.end():]
    return text, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    public = json.loads((ROOT / 'public/data.json').read_text())
    prefs = set(public['prefectures'])
    code_path, code_receipt = original(CODE_URL, args.fetch)
    codes = municipality_codes(code_path)
    sources, originals, all_records, all_totals = [], [], {}, {}
    for year, url in URLS.items():
        path, receipt = original(url, args.fetch)
        records, totals, grand = extract_bbox(path, year, prefs)
        text, independent = extract_layout(path, year, prefs)
        selected = {name: {field: v[field] for field in ('plans', 'amount_thousand_yen')} for (_, name), v in records.items()}
        if selected != independent:
            raise ValueError('Independent layout parser differs from bbox extraction')
        if '令和' + ('７' if year == 2025 else '８') + '年度' not in text:
            raise ValueError('Unexpected fiscal-year header')
        cover = re.sub(r'\s+', '', text.split('\f')[0])
        expected_date = '令和７年10月24日' if year == 2025 else '令和８年６月26日'
        if expected_date not in cover:
            raise ValueError('Unexpected publication date in dated announcement PDF cover')
        for name in prefs:
            current = next(r for r in public['rows'] if r['id'] == 'care-' + name)
            if current['amount' + str(year)] != records[('都道府県', name)]['amount_thousand_yen'] / 1000:
                raise ValueError('Existing prefectural evidence differs from fetched original')
        all_records[year] = records
        all_totals[str(year)] = dict(subtotals=totals, total=grand)
        sid = f'care-cities-{year}'
        excerpt = '単位：千円。指定都市・中核市の掲載値。\n' + '\n'.join(
            f'{g} {n} 計画数 {v["plans"]} {v["column"]} {v["amount_thousand_yen"]:,}'
            for (g, n), v in records.items() if g != '都道府県')
        excerpt += '\n' + ('下線部はこれまでの内示から変更があった記載。' if year == 2025 else
                           '計画数が０の自治体は協議がなかったもの。国土強靱化対策分は全体の内数。')
        sources.append(dict(id=sid, title=f'{year}年度地域介護・福祉空間整備等施設整備交付金：一次協議・市別',
                            url=url, locator='PDF2頁、指定都市・中核市欄。PDF1頁の発表日を照合。',
                            kind='予算・議員資料', accessed=DATE, published='2025-10-24' if year == 2025 else '2026-06-26',
                            retrieved_via='公式PDF原本、bboxとlayoutの独立抽出照合', excerpt=excerpt,
                            sha256_extracted_text=sha(excerpt.encode())))
        values = {f'care-city-{g}-{n}': {'amount' + str(year): v['amount_thousand_yen'] / 1000}
                  for (g, n), v in records.items() if g != '都道府県'}
        originals.append({**{k: v for k, v in receipt.items() if k != 'status'}, 'source_ids': [sid],
                          'download_status': 'downloaded', 'verification': dict(status='matched', checked_at=DATE,
                          method='bbox座標と独立layout行解析が全129主体で一致。3枠の金額・計画数小計と総合計に完全一致。既存47県額も維持。',
                          locator=sources[-1]['locator'], row_ids=list(values), fields=['amount' + str(year)], values=values)})
    city_keys = {key for key in all_records[2025] if key[0] != '都道府県'}
    if city_keys != {key for key in all_records[2026] if key[0] != '都道府県'}:
        raise ValueError('City recipient sets differ; review missingness before comparing')
    code_excerpt, geography = [], {}
    for _, name in sorted(city_keys):
        matched = codes.get(name, [])
        if len(matched) != 1 or matched[0]['prefecture'] not in prefs:
            raise ValueError('Municipality reference does not resolve city uniquely')
        geography[name] = matched[0]
        v = matched[0]
        code_excerpt.append(f'{v["code"]} {v["prefecture"]} {name} XLSX行{v["xlsx_row"]}')
    code_text = '総務省全国地方公共団体コード：R6.1.1現在の団体。市名の完全・一意一致。現在の区割り・議員や配分時点の対応を認定しない。\n' + '\n'.join(code_excerpt)
    sources.append(dict(id='care-city-municipality-codes', title='総務省：全国地方公共団体コード・市の県帰属照合',
                        url=CODE_URL, locator='R6.1.1現在の団体、A〜C列・対象82市', kind='地域対応資料',
                        accessed=DATE, published=None, reference_date='2024-01-01', retrieved_via='公式XLSXのXML表セルを抽出',
                        excerpt=code_text, sha256_extracted_text=sha(code_text.encode())))
    originals.append({**{k: v for k, v in code_receipt.items() if k != 'status'}, 'source_ids': ['care-city-municipality-codes'],
                      'download_status': 'downloaded', 'verification': dict(status='downloaded_only', checked_at=DATE,
                      method='XLSXの市名・県名・6桁コードを82市で完全・一意一致。金額照合とは別。',
                      locator=sources[-1]['locator'], row_ids=[], fields=[], values={})})
    rows = []
    note = ('2025年10月24日更新と2026年6月26日公表の一次協議資料を参考比較。'
            '2025指定都市は今回計画額、中核市は内示額、2026は計画額。年度全体・当初予算総額・交付決定・実支出とは区別する。'
            '両年度の明示数値0は原本どおり保持。2026原本は計画数0を協議なしと注記し、交付・執行ゼロとは認定しない。'
            '2025は従前内示からの変更を含む。財源・補正・繰越・追加内示・採択対象の同範囲性は未照合。'
            '47県分と20指定都市・62中核市分は別枠。親総合計と内訳、国土強靱化内数は足し合わせない。'
            '総務省2024年1月1日原本の市名に一意一致し県帰属を確認。現行境界・小選挙区・配分決定時点の関係者は未照合。')
    for group, name in sorted(city_keys):
        geo = geography[name]
        row = dict(id=f'care-city-{group}-{name}', ministry='厚生労働省', program='地域介護・福祉空間整備等施設整備交付金',
                   region=name, prefecture=geo['prefecture'], basis='一次協議内示・市別（公表・更新時点差）',
                   account='財源区分未確認', unit='百万円', scope=f'{group}分（県分とは別枠）',
                   period2025='2025年10月24日更新の一次協議', period2026='2026年6月26日公表の一次協議',
                   source_ids=['care-cities-2025', 'care-cities-2026', 'care-announcement-2025', 'care-announcement-2026', 'care-city-municipality-codes'],
                   evidence_status='公式原本集計済み', comparability='参考・更新時点差', note=note,
                   geography_status='独立公式XLSXの市名・県帰属を一意照合（2024年1月1日基準）', municipality_code=geo['code'])
        for year in (2025, 2026):
            v = all_records[year][(group, name)]
            row['amount' + str(year)] = v['amount_thousand_yen'] / 1000
            row['plans' + str(year)] = v['plans']
            row['amount_column' + str(year)] = v['column']
        rows.append(row)
    report = dict(schema_version=1, checked_at=DATE, sources=sources, originals=originals, rows=rows,
                  geographic_reference_date='2024-01-01', reconciliation=all_totals,
                  independent_checks=dict(bbox_vs_layout_equal=True, printed_numeric_recipient_rows_per_year=129,
                                          city_rows_per_year=82, existing_prefectural_amounts_equal=True,
                                          subtotal_amounts_and_plan_counts_equal=True, city_prefecture_unique_matches=82),
                  research_notes=[dict(ministry='厚生労働省', status='一次協議の指定都市・中核市82行を参考収載',
                                       note=note, source_ids=[s['id'] for s in sources])])
    if args.write:
        for source in sources:
            (ROOT / 'data/source-text' / (source['id'] + '.txt')).write_text(source['excerpt'])
        (ROOT / 'data/reviewed-care-cities.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    else:
        saved = json.loads((ROOT / 'data/reviewed-care-cities.json').read_text())
        if report != saved:
            raise ValueError('Re-extracted report differs from committed reviewed evidence; review before --write')
        for source in sources:
            if sha((ROOT / 'data/source-text' / (source['id'] + '.txt')).read_bytes()) != source['sha256_extracted_text']:
                raise ValueError('Saved extracted text digest differs from reviewed source')
    print(json.dumps(dict(rows=len(rows), independently_checked_values=164, totals=all_totals, written=args.write), ensure_ascii=False))


if __name__ == '__main__':
    main()
