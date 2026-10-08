"""Reconcile FDMA decision-year summaries with separately funded carryovers.

The existing R8 initial-budget comparison remains unchanged. Default validation
uses committed evidence; --extract requires the ignored official cache plus
Poppler/pdfplumber. --fetch makes one checked request per official URL, preserves
inherited proxy/TLS, and continues after failure. --write follows manual review.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import unicodedata
from urllib.request import Request, build_opener

from fetch_sources import MAX_BYTES, failure_details
from verify_fdma_facilities import Redirect, checked_fdma, extract_tables, extract_poppler
from verify_grants_originals import table_rows, column, integer

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/fdma-reconciliation'
OUTPUT = ROOT / 'data/reviewed-fdma-reconciliation.json'
DATE = '2026-10-09'
URLS = {
    'monthly': 'https://www.fdma.go.jp/publication/ugoki/items/rei_0806_11.pdf',
    'index': 'https://www.fdma.go.jp/pressrelease/info/',
    'initial': 'https://www.fdma.go.jp/pressrelease/info/items/R8sisetu.pdf',
    'carryover': 'https://www.fdma.go.jp/pressrelease/info/items/R7sisetuv2.pdf',
}
META = ('url', 'final_url', 'retrieved_at_utc', 'bytes', 'sha256_original', 'content_type')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fetch():
    policy = json.loads(Path('/etc/codex/network-policy.json').read_text())['http_network_policy']
    assert policy['type'] == 'unrestricted' or 'www.fdma.go.jp' in {r['host'] for r in policy['egress_rules']}
    CACHE.mkdir(parents=True, exist_ok=True)
    for url in URLS.values():
        receipt = dict(url=url, retrieved_at_utc=datetime.now(timezone.utc).isoformat())
        try:
            with build_opener(Redirect()).open(Request(checked_fdma(url), headers={
                    'User-Agent': 'japan-budget-tracker/0.1 (public official budget research)'}), timeout=30) as response:
                raw = response.read(MAX_BYTES + 1)
                assert 0 < len(raw) <= MAX_BYTES
                receipt.update(status='downloaded', http_status=response.status,
                    final_url=checked_fdma(response.geturl()), bytes=len(raw),
                    sha256_original=sha(raw), content_type=response.headers.get_content_type())
            (CACHE / (sha(url.encode()) + '.' + receipt['sha256_original'] + '.bin')).write_bytes(raw)
        except Exception as error:
            receipt.update(status='blocked_or_failed', **failure_details(error))
        with (CACHE / 'manifest.jsonl').open('a') as stream:
            stream.write(json.dumps(receipt) + '\n')
        print(json.dumps({k: receipt[k] for k in ('url', 'status', 'failure_category', 'http_status') if k in receipt}))


def original(key):
    receipts = [json.loads(line) for line in (CACHE / 'manifest.jsonl').read_text().splitlines()]
    receipt = next(r for r in reversed(receipts) if r['url'] == URLS[key] and r['status'] == 'downloaded')
    path = CACHE / (sha(receipt['url'].encode()) + '.' + receipt['sha256_original'] + '.bin')
    raw = path.read_bytes()
    assert sha(raw) == receipt['sha256_original'] and len(raw) == receipt['bytes']
    checked_fdma(receipt['url']); checked_fdma(receipt['final_url'])
    receipt = {k: receipt[k] for k in META}
    if OUTPUT.exists():
        saved = json.loads(OUTPUT.read_text())
        prior = next((r for r in saved['originals'] + saved['historical_original_rechecks'] if r['url'] == receipt['url']), None)
        if prior:
            assert all(prior[k] == receipt[k] for k in META if k != 'retrieved_at_utc'), 'Original changed; review before publication'
            receipt['retrieved_at_utc'] = prior['retrieved_at_utc']
    return path, receipt


def pdf_text(path, mode='-layout'):
    return subprocess.check_output(['pdftotext', mode, str(path), '-'], text=True)


def compact(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))


def categories(facilities):
    totals = Counter()
    for facility in facilities:
        name = facility['facility']
        matches = [label for label in ('耐震性貯水槽', '防火水槽', '活動火山対策避難施設', '高機能消防指令センター') if name.startswith(label)]
        assert len(matches) == 1, name
        totals[matches[0]] += facility['quantity']
    return {label: totals[label] for label in ('耐震性貯水槽', '防火水槽', '活動火山対策避難施設', '高機能消防指令センター')}


def carryover_poppler(path):
    # This PDF uses a wider facility column than the initial-budget PDF.
    # Recipient words begin at 9.9% of the width; never reuse its initial-table
    # coordinates, which would accidentally select facility names instead.
    pending, records, totals = None, [], []
    for page, width, y, words in table_rows(path):
        name = column(words, width * .09, width * .27)
        if name.endswith(('市', '町')):
            assert pending is None
            pending = name
        amount = column(words, width * .90, width)
        if re.fullmatch(r'\d+(?:,\d{3})*', amount):
            if pending is not None:
                records.append((pending, integer(amount)))
                pending = None
            else:
                totals.append(integer(amount))
    assert pending is None and len(totals) == 1
    return records, totals[0]


def build():
    old = json.loads((ROOT / 'data/reviewed-fdma-facilities.json').read_text())
    prefs = set(json.loads((ROOT / 'public/data.json').read_text())['prefectures'])
    paths, receipts = {}, {}
    for key in URLS:
        paths[key], receipts[key] = original(key)
    historical = []
    for key in ('initial', 'index'):
        previous = next(r for r in old['originals'] if r['url'] == URLS[key])
        assert all(previous[k] == receipts[key][k] for k in META if k != 'retrieved_at_utc'), 'Baseline original changed'
        historical.append(dict(**receipts[key], status='先行原本SHA・サイズ・最終URL・種別一致',
                               historical_retrieved_at_utc=previous['retrieved_at_utc'], historical_evidence_preserved=True))
    html = paths['index'].read_text()
    assert '/pressrelease/info/items/R7sisetuv2.pdf' in html and '令和７年度当初予算（本省繰越）' in html
    assert '/pressrelease/info/items/R8sisetu.pdf' in html
    tables, facilities, totals, counts = {}, {}, {}, {}
    for key in ('initial', 'carryover'):
        tables[key], facilities[key], totals[key] = extract_tables(paths[key], prefs)
        independent, grand = extract_poppler(paths[key], 2026) if key == 'initial' else carryover_poppler(paths[key])
        assert independent == [(r['recipient'], r['amount_thousand_yen']) for r in tables[key]]
        assert grand == totals[key]['amount_thousand_yen']
        counts[key] = categories(facilities[key])
        text = compact(pdf_text(paths[key]))
        assert '令和8年4月21日' in text
        assert '令和8年度当初予算' in text if key == 'initial' else '令和7年度当初予算(本省繰越)' in text
    assert totals['initial'] == dict(quantity=148, amount_thousand_yen=1108214, recipient_sum_thousand_yen=1108214)
    assert totals['carryover'] == dict(quantity=8, amount_thousand_yen=48272, recipient_sum_thousand_yen=48272)
    assert [(r['prefecture'], r['recipient'], r['amount_thousand_yen']) for r in tables['carryover']] == [
        ('山形県', '尾花沢市', 8536), ('富山県', '立山町', 19296), ('徳島県', '美馬市', 12264), ('大分県', '宇佐市', 8176)]
    # Independent raw-order extraction confirms the printed summary, distinct
    # from either grant decision table's structured cells.
    for mode in ('-layout', '-raw'):
        text = compact(pdf_text(paths['monthly'], mode))
        for expected in ('4月21日付け', '11億5,648万6千円', '耐震性貯水槽151件', '防火水槽(林野分)2件',
                         '活動火山対策避難施設1件', '高機能消防指令センター2件'):
            assert expected in text, expected
    summary_count = {'耐震性貯水槽': 151, '防火水槽': 2, '活動火山対策避難施設': 1, '高機能消防指令センター': 2}
    assert {k: counts['initial'][k] + counts['carryover'][k] for k in summary_count} == summary_count
    assert totals['initial']['amount_thousand_yen'] + totals['carryover']['amount_thousand_yen'] == 1156486
    assert totals['initial']['quantity'] + totals['carryover']['quantity'] == sum(summary_count.values()) == 156
    sources, originals = [], []
    texts = {
        'monthly': '消防の動き2026年6月号・印刷11頁。令和8年度における交付決定の状況、4月21日付け。\n消防防災施設整備費補助金11億5,648万6千円（1,156,486千円）。\n耐震性貯水槽151件、防火水槽（林野分）2件、活動火山対策避難施設1件、高機能消防指令センター2件。\n別制度の緊急消防援助隊設備整備費補助金を混ぜない。月刊の本文は当初財源限定とは記載しない。',
        'carryover': '2026年4月21日決定。令和7年度当初予算（本省繰越）に係る消防防災施設整備費補助金。単位：件、千円。\n' + '\n'.join(
            f'{r["prefecture"]} {r["recipient"]} {f["facility"]} 数量{f["quantity"]} 補助金額{f["amount_thousand_yen"]:,}'
            for r in tables['carryover'] for f in r['facilities']) + '\n合計数量8、補助金額48,272、団体計48,272。財源は2025年度当初本省繰越、決定日は2026年度。既存2026当初行へ加算しない。',
    }
    for key, title in [('monthly', '消防庁月刊2026年6月：4月21日交付決定概要'), ('carryover', '消防庁：2025年度当初本省繰越・2026年4月21日交付決定')]:
        sid = 'fdma-reconciliation-' + key
        sources.append(dict(id=sid, title=title, url=URLS[key], kind='交付決定概要・財源差の照合', accessed=DATE, published=None,
            document_date='2026-06' if key == 'monthly' else '2026-04-21',
            document_date_precision='month' if key == 'monthly' else 'day',
            locator='PDF1頁・印刷11頁の交付決定額と対象施設' if key == 'monthly' else 'PDF1頁・4団体と合計',
            retrieved_via='公式PDF原本・layout/raw抽出と画像独立確認、繰越罫線セルとPoppler座標抽出照合',
            excerpt=texts[key], sha256_extracted_text=sha(texts[key].encode())))
        originals.append(dict(**receipts[key], source_ids=[sid], verification=dict(status='downloaded_only', checked_at=DATE,
            method='決定日・総額・施設件数・団体内訳を照合。既存年度比較の金額更新ではない。', locator=sources[-1]['locator'], row_ids=[], fields=[])))
    source_ids = [s['id'] for s in sources] + ['fdma-facilities-first-2026', 'fdma-facilities-index-2026']
    note = ('月刊6月号の4月21日概要1,156,486千円・156件は、既存2026当初1,108,214千円・148件と2025当初本省繰越48,272千円・8件の和に一致。'
            '繰越は尾花沢市8,536千円・2件、立山町19,296千円・1件、美馬市12,264千円・3件、宇佐市8,176千円・2件。'
            '耐震性貯水槽144+7=151、防火水槽2+0=2、火山避難施設0+1=1、指令センター2+0=2も一致。'
            '財源年度と決定年度の範囲差を確認。月刊本文は財源別内訳を明記しないため照合結果として表示し、当初額の訂正・欠落や政治的影響を認定しない。'
            '現行索引とR8初回原本は先行SHA・byte数・最終URL・種別に一致。既存124比較行と当初予算総額を保持し、繰越を2026当初額へ加算せず、交付決定を支出済額へ読み替えない。')
    return dict(schema_version=1, checked_at=DATE, sources=sources, originals=originals, rows=[],
        historical_original_rechecks=historical,
        carryover_observations=[dict(funding_fiscal_year=2025, funding_basis='当初予算・本省繰越', decision_fiscal_year=2026,
            decision_date='2026-04-21', prefecture=r['prefecture'], recipient=r['recipient'], amount_thousand_yen=r['amount_thousand_yen'],
            facilities=r['facilities'], is_cash_spending=False, excluded_from_initial_budget_comparison=True, source_ids=['fdma-reconciliation-carryover']) for r in tables['carryover']],
        reconciliation=dict(status='金額と施設件数の範囲差一致', initial_budget_total=totals['initial'], carryover_total=totals['carryover'],
            decision_summary_amount_thousand_yen=1156486, decision_summary_quantity=156, difference_thousand_yen=48272,
            initial_category_quantities=counts['initial'], carryover_category_quantities=counts['carryover'], summary_category_quantities=summary_count,
            independent_recipient_and_total_extraction_equal=True, monthly_layout_raw_equal=True, existing_comparison_modified=False),
        research_notes=[dict(ministry='総務省', agency='消防庁', status='決定概要と当初・繰越の範囲差を金額・件数で照合', note=note, source_ids=source_ids)],
        data_quality_findings=[dict(ministry='総務省', recipient='消防庁・施設補助金', status='当初と繰越の範囲差を照合',
            difference_million_yen=48.272, inference_limit=note, source_ids=source_ids)],
        limitations=['月刊の本文に財源別内訳はない。公式の別表を金額・施設件数・決定日・現行索引で突合した照合結果。',
            '2025財源の繰越を2026当初配分や同期間支出額へ読み替えない。各決定額は実支出ではない。'])


def validate():
    report = json.loads(OUTPUT.read_text())
    assert report['rows'] == [] and report['reconciliation']['existing_comparison_modified'] is False
    for s in report['sources']:
        checked_fdma(s['url'])
        assert sha(s['excerpt'].encode()) == s['sha256_extracted_text']
        assert (ROOT / 'data/source-text' / (s['id'] + '.txt')).read_text() == s['excerpt'] + '\n'
    c = report['reconciliation']
    assert c['initial_budget_total']['amount_thousand_yen'] + c['carryover_total']['amount_thousand_yen'] == c['decision_summary_amount_thousand_yen']
    assert c['initial_budget_total']['quantity'] + c['carryover_total']['quantity'] == c['decision_summary_quantity']
    assert sum(c['initial_category_quantities'].values()) == c['initial_budget_total']['quantity']
    assert sum(c['carryover_category_quantities'].values()) == c['carryover_total']['quantity']
    assert {k: c['initial_category_quantities'][k] + c['carryover_category_quantities'][k]
            for k in c['summary_category_quantities']} == c['summary_category_quantities']
    assert len(report['carryover_observations']) == 4
    assert sum(o['amount_thousand_yen'] for o in report['carryover_observations']) == c['difference_thousand_yen']
    assert sum(f['quantity'] for o in report['carryover_observations'] for f in o['facilities']) == c['carryover_total']['quantity']
    assert all(o['funding_fiscal_year'] == 2025 and o['decision_fiscal_year'] == 2026 and not o['is_cash_spending']
               and o['excluded_from_initial_budget_comparison'] for o in report['carryover_observations'])
    previous = json.loads((ROOT / 'data/reviewed-fdma-facilities.json').read_text())
    for r in report['historical_original_rechecks']:
        old = next(o for o in previous['originals'] if o['url'] == r['url'])
        assert all(r[k] == old[k] for k in META if k != 'retrieved_at_utc')
        assert r['historical_retrieved_at_utc'] == old['retrieved_at_utc']
    print(json.dumps(c, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--extract', action='store_true')
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    if args.fetch:
        fetch()
    if args.extract or args.write:
        result = build()
        if args.write:
            OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
            for s in result['sources']:
                (ROOT / 'data/source-text' / (s['id'] + '.txt')).write_text(s['excerpt'] + '\n')
        else:
            assert result == json.loads(OUTPUT.read_text()), 'Reviewed evidence differs from original extraction'
    validate()
