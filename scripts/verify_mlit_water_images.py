"""Verify reviewed MLIT image-table cells; no network by default.

The committed review is a direct visual transcription of the official PDF,
created independently of Tesseract. --ocr rerenders original pages and repeats
cell extraction. This is two extraction methods on one original, not evidence
from two independent publishers. Debt commitments and bracketed allocation
subsets are excluded. --write generates the public additions after review.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.error import HTTPError, URLError

from fetch_sources import failure_details
from verify_mlit_originals import ROOT, fetch, original_path
from verify_mlit_water import ENTRIES, PUBLISHED, url_for, historical_receipt

REVIEW = ROOT / 'data/mlit-water-image-review.json'
OCR = ROOT / 'data/mlit-water-image-ocr.json'
OUT = ROOT / 'data/reviewed-mlit-water-images.json'
INDEPENDENT = ROOT / 'data/mlit-water-independent-review.json'
CACHE = ROOT / '.cache/water-review'
CHECKED_AT = '2026-10-09'


def receipt_for(url):
    return historical_receipt(url, OUT)


def read_review():
    reviewed = json.loads(REVIEW.read_text())
    independent = json.loads(INDEPENDENT.read_text())
    assert [t['year'] for t in reviewed['tables']] == [2025, 2026]
    for t in reviewed['tables']:
        second = independent['tables'][str(t['year'])]
        assert second['sha256_original'] == t['sha256_original']
        assert second['url'] == t['url']
        assert second['subsidy_pdf_page'] == t['subsidy_pdf_page'] and second['direct_pdf_page'] == t['direct_pdf_page']
        assert second['subsidy'] == t['subsidy'], 'Independent prefecture labels and values differ'
        assert second['direct'] == t['direct'], 'Independent bureau labels and values differ'
        # Printed table order differs from prefecture-code order. Check labels,
        # not only amounts and totals, to prevent a silent positional join.
        aliases = {re.sub('[都府県]$', '', p): p for p in t['subsidy']}
        assert [aliases[label] for label in t['printed_subsidy_region_order']] == list(t['subsidy'])
        assert len(t['direct']) == 10 and len(t['subsidy']) == 47
        for family, columns in [('direct', 5), ('subsidy', 3)]:
            assert all(len(v) == columns for v in t[family].values())
            totals = t[f'published_{family}_totals']
            for c, total in enumerate(totals):
                values = [r[c] for r in t[family].values() if r[c] is not None]
                assert abs(sum(values) - total) <= len(values) / 2 + 1
        for values in t['direct'].values():
            assert abs(sum(v for v in values[:4] if v is not None) - values[4]) <= 2
        assert t['published_subsidy_totals'][2] + t['nonprefecture_sewer_business_cost'] == t['published_sewer_total_with_nonprefecture']
    return reviewed


def crop_jobs(reviewed):
    """Fixed coordinates for page rendered by Poppler with scale-to 2200."""
    from PIL import Image
    CACHE.mkdir(parents=True, exist_ok=True)
    jobs = []
    for t in reviewed['tables']:
        year = t['year']
        for family in ['direct', 'subsidy']:
            page = t[f'{family}_pdf_page']
            prefix = CACHE / f'water{str(year)[2:]}'
            output = Path(f'{prefix}-{page:02}.png')
            subprocess.run(['pdftoppm', '-f', str(page), '-l', str(page), '-scale-to', '2200', '-png', str(original_path(t['url'])), str(prefix)], check=True)
            im = Image.open(output)
            assert im.size == (1556, 2200)
            if family == 'subsidy':
                xs = [(338, 445), (572, 679), (1157, 1263)] if year == 2025 else [(322, 438), (564, 675), (1169, 1278)]
                first, step, half = (265.5, 28.70, 9) if year == 2025 else (293.5, 28.79, 9)
            else:
                xs = [(425, 592), (605, 773), (785, 953), (965, 1133), (1145, 1313)] if year == 2025 else [(442, 602), (615, 773), (787, 946), (959, 1117), (1130, 1290)]
                first, step, half = (432.5, 114.6, 10) if year == 2025 else (434, 109.7, 10)
            for i, (region, values) in enumerate(t[family].items()):
                ri = i if family == 'subsidy' or i < 8 else i + 1  # skip printed subtotal
                for c, value in enumerate(values):
                    cy = first + ri * step
                    box = (xs[c][0], round(cy - half), xs[c][1], round(cy + half))
                    crop = im.crop(box).resize((4 * (box[2] - box[0]), 4 * (box[3] - box[1])))
                    path = CACHE / f'{year}-{family}-{i}-{c}.png'
                    crop.save(path)
                    jobs.append((year, family, region, c, value, path, box))
    return jobs


def ocr_cell(job):
    year, family, region, column, value, path, box = job
    env = dict(os.environ, OMP_THREAD_LIMIT='1')
    result = subprocess.run(['tesseract', str(path), 'stdout', '--psm', '7', '-c', 'tessedit_char_whitelist=0123456789,-'], capture_output=True, text=True, check=True, env=env)
    raw = result.stdout.strip()
    digits = re.sub('[ ,]', '', raw)
    amount = int(digits) if digits.isdigit() else None
    return dict(year=year, family=family, region=region, column=column, reviewed=value,
                ocr=amount, ocr_raw=raw, box=list(box), match=amount == value,
                # PNG digest only; binaries remain in ignored cache.
                sha256_cell_png=hashlib.sha256(path.read_bytes()).hexdigest())


def verify_ocr(reviewed, ledger):
    """Check every cell, retaining rejected OCR and its original-image review."""
    lookup = {(r['year'], r['family'], r['region'], r['column']): r for r in ledger['cells']}
    expected = set()
    for t in reviewed['tables']:
        for family in ['direct', 'subsidy']:
            for region, values in t[family].items():
                for column, value in enumerate(values):
                    key = (t['year'], family, region, column)
                    expected.add(key)
                    cell = lookup[key]
                    assert cell['reviewed'] == value
                    assert cell['match'] == (cell['ocr'] == value)
                    assert re.fullmatch('[a-f0-9]{64}', cell['sha256_cell_png'])
                    if not cell['match'] or value is None and cell['ocr_raw'] != '-':
                        assert cell.get('visual_review_status') == '公式原本セルを再確認'
                        assert cell.get('visual_review_value') == value
    assert expected == set(lookup) and len(expected) == len(ledger['cells']) == 382


def build(reviewed, ledger):
    sources, originals, rows, checks = [], [], [], {}
    data = {t['year']: t for t in reviewed['tables']}
    for year, t in data.items():
        original = receipt_for(t['url'])
        assert original['sha256_original'] == t['sha256_original']
        press = original_path(ENTRIES[year]).read_text()
        assert ('令和7年4月1日' if year == 2025 else '令和8年4月7日') in press
        assert t['url'].split('/')[-1] in press
        sid = f'mlit-water-image-verified-{year}'
        excerpt = [f'{year}年度 水管理・国土保全局 当初配分 地域表（単位：百万円）']
        for family in ['direct', 'subsidy']:
            excerpt.append(f'{family}：' + '／'.join(t[f'{family}_columns']))
            excerpt.extend(region + '：' + '／'.join('−' if v is None else str(v) for v in values) for region, values in t[family].items())
        excerpt += ['配分額は工事諸費を除いた事業費。ダムは利水者負担金を含む。四捨五入により合計が合わない場合がある。',
                    '直轄表の括弧は一括配分の内数で合算しない。国庫債務負担行為表・水資源機構等の表外国費・災害復旧は別範囲。',
                    f'日本下水道事業団の下水道事業費 {t["nonprefecture_sewer_business_cost"]} 百万円は県へ配賦しない。']
        text = '\n'.join(excerpt)
        locator = f'PDF{t["direct_pdf_page"]}頁の直轄表（上段、括弧の内数を除く）・PDF{t["subsidy_pdf_page"]}頁の補助表の河川／砂防／下水道列。'
        sources.append(dict(id=sid, title=f'{year}年度水管理・国土保全局の当初地域配分（画像原本照合・事業費）', url=t['url'],kind='予算・地域配分資料',
                            accessed=CHECKED_AT,published=PUBLISHED[year],published_date_evidence='公式当初配分発表頁の公表日と当該PDFリンクを原本確認',
                            document_date=f'{year}-04',document_date_precision='month',locator=locator,
                            retrieved_via='公式PDF画像の2回の独立直接転記・地域名対応とセルOCR抽出を照合。別発行出典による照合ではない。',
                            sha256_extracted_text=hashlib.sha256(text.encode()).hexdigest(),excerpt=text))
        checks[str(year)] = dict(direct_published_totals=t['published_direct_totals'],subsidy_published_totals=t['published_subsidy_totals'],
                                direct_rounded_sums=[sum(v[c] or 0 for v in t['direct'].values()) for c in range(5)],
                                subsidy_rounded_sums=[sum(v[c] or 0 for v in t['subsidy'].values()) for c in range(3)],
                                nonprefecture_sewer_business_cost=t['nonprefecture_sewer_business_cost'])
    for family, labels in [('direct', ['河川', 'ダム', '砂防', '海岸']), ('subsidy', ['河川', '砂防', '下水道'])]:
        for region in data[2025][family]:
            assert region in data[2026][family]
            for column, label in enumerate(labels):
                a, b = [data[y][family][region][column] for y in [2025, 2026]]
                rid = f'mlit-water-image-{family}-{label}-{region}'
                row = dict(id=rid, ministry='国土交通省', program=f'{label}関係' + ('直轄' if family=='direct' else '補助') + '事業（事業費・国費ではない）',
                    region=region,prefecture=region if family=='subsidy' else None,basis='当初配分（事業費）',account='会計別未分解',unit='百万円',
                    amount2025=a,amount2026=b,source_ids=[f'mlit-water-image-verified-{y}' for y in [2025,2026]],
                    period2025='2025年度当初配分',period2026='2026年度当初配分',evidence_status='公式原本照合済み',
                    comparability='同範囲' if a is not None and b is not None else '原本ダッシュを含む',
                    scope='都道府県別の当該事業配分（受取自治体のみではない）' if family=='subsidy' else '地方支分部局等の広域管内・当該事業配分',
                    precision='百万円・四捨五入。原本のダッシュはnullを保持し、ゼロと認定しない。',
                    note='工事諸費を除く事業費であり国費・交付決定・支出済額ではない。国庫債務負担行為と全国総額・小計・括弧内数・他財源を合算しない。会計区分、箇所・進捗の変化、個別申請・決定時点の関係者は未照合。')
                if family=='direct':row['note'] += '広域管内を県へ割り当てず、一括配分の括弧は上段に含まれる内数。'
                if label=='ダム':row['note'] += '利水者負担金を含み、水資源開発事業交付金の表外国費は含めない。'
                if label=='下水道':row['note'] += '上下水道・水道の列と区別。日本下水道事業団の非県額を県へ配賦しない。'
                for year, amount in [(2025,a),(2026,b)]:
                    row[f'amount_status{year}'] = '原本数値照合済み' if amount is not None else '原本ダッシュ・ゼロ認定なし'
                    ordinal = list(data[year][family]).index(region)
                    printed = data[year]['printed_subsidy_region_order'][ordinal] if family=='subsidy' else region
                    row[f'source_locator{year}'] = dict(page=data[year][f'{family}_pdf_page'],printed_label=printed,region_row_ordinal=ordinal+1,column=label,cell='上段' if family=='direct' else '当該列')
                rows.append(row)
    for year,t in data.items():
        original = receipt_for(t['url'])
        rids = [r['id'] for r in rows]
        originals.append(dict(**original, source_ids=[f'mlit-water-image-verified-{year}'], verification=dict(
            status='matched',checked_at=CHECKED_AT,method='2回の独立原本画像転記で全地域名・数値を照合し、セルOCRも検査。OCR誤読・ダッシュ誤検出は画像セルを再レビューし、採用しないOCR候補も証跡に保持。全国小計・丸め・重複除外を検算。',
            locator=sources[year-2025]['locator'],row_ids=rids,fields=[f'amount{year}'],values={r['id']:{f'amount{year}':r[f'amount{year}']} for r in rows})))
    return dict(schema_version=1,checked_at=CHECKED_AT,sources=sources,originals=originals,rows=rows,
        research_notes=[dict(ministry='国土交通省',status='水管理画像の地域配分を原本照合',source_ids=[s['id'] for s in sources],
            note='前回留保した画像原本を、2回の独立直接転記・地域名対応、セルOCR、全国小計の検算で確認。直轄4事業×10管内40行と補助の河川・砂防・下水道×47県141行、計181行を追加。水道・上下水道列、国庫債務負担行為、個別箇所集計は未収載。別出典からの地域別再集計は未完了。原表は県コード順ではなく管内順で、県名を直接照合。ダッシュはゼロ化せず、国費・実支出・括弧内数・事業団を混ぜない。')],
        verification_summary=dict(new_rows=len(rows),fully_nonmissing_rows=sum(r['amount2025'] is not None and r['amount2026'] is not None for r in rows),
            rows_with_original_dash=sum(r['amount2025'] is None or r['amount2026'] is None for r in rows),reviewed_cells=len(ledger['cells']),
            ocr_exact_matches=sum(c['match'] for c in ledger['cells']),published_tables=checks,
            review_sha256=hashlib.sha256(REVIEW.read_bytes()).hexdigest(),ocr_ledger_sha256=hashlib.sha256(OCR.read_bytes()).hexdigest(),
            independent_review_sha256=hashlib.sha256(INDEPENDENT.read_bytes()).hexdigest()),
        limitations=['画像原本上の2抽出方法であり、独立した別発行原本からの集計ではない。','数値一致は制度範囲の連続性・地域帰属・年度執行の確認とは別。','金額の増減から政治的影響を認定しない。'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch',action='store_true')
    parser.add_argument('--ocr',action='store_true',help='Repeat image-cell extraction; requires Poppler, Pillow and Tesseract')
    parser.add_argument('--write',action='store_true')
    args=parser.parse_args()
    reviewed=read_review()
    if args.fetch:
        for url in [*[t['url'] for t in reviewed['tables']], *ENTRIES.values()]:
            try:
                fetch(url)
            except (HTTPError, URLError, TimeoutError, ValueError) as error:
                print(json.dumps(dict(url=url,status='blocked_or_failed',**failure_details(error)),ensure_ascii=False))
    if args.ocr:
        previous=json.loads(OCR.read_text()) if OCR.exists() else {'cells':[]}
        old={(r['year'],r['family'],r['region'],r['column']):r for r in previous['cells']}
        with ThreadPoolExecutor(max_workers=4) as pool:cells=list(pool.map(ocr_cell,crop_jobs(reviewed)))
        for cell in cells:
            baseline=old.get((cell['year'],cell['family'],cell['region'],cell['column']),{})
            # Preserve the explicit visual resolution only if exact crop and candidates match.
            if all(cell.get(k)==baseline.get(k) for k in ['sha256_cell_png','reviewed','ocr','ocr_raw']):
                for k in ['visual_review_status','visual_review_value','visual_review_reason']:
                    if k in baseline:cell[k]=baseline[k]
        candidate=dict(schema_version=1,checked_at=CHECKED_AT,method='Poppler画像をセル単位に抽出、Tesseract数値限定PSM7。手転記とは独立の抽出。',cells=cells)
        (CACHE/'ocr-candidate.json').write_text(json.dumps(candidate,ensure_ascii=False,indent=2)+'\n')
        verify_ocr(reviewed,candidate)
        current = {(r['year'],r['family'],r['region'],r['column']):r for r in candidate['cells']}
        assert current==old,'OCR changed; review candidate before replacing committed ledger'
    ledger=json.loads(OCR.read_text())
    verify_ocr(reviewed,ledger)
    result=build(reviewed,ledger)
    if args.write:OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    else:assert result==json.loads(OUT.read_text()),'Reviewed report differs; review before writing'
    print(json.dumps(result['verification_summary'],ensure_ascii=False))


if __name__=='__main__':main()
