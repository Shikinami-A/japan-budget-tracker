"""Verify prefectural plans separately from notification rounds and cash spending.

Use --fetch for bounded official requests and --write only after review. The
default command reproduces committed evidence from the ignored local cache.
"""
import argparse
import hashlib
import json
import re
import subprocess
import time
import unicodedata
from pathlib import Path
from urllib.request import Request, build_opener

from fetch_sources import CheckedRedirect, HOSTS, MAX_BYTES, checked, failure_details

HOSTS.add('www.pref.fukushima.lg.jp')
ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache' / 'reconstruction-followup'
OUTPUT = ROOT / 'data' / 'reviewed-reconstruction-followup.json'
BASELINE = ROOT / 'data' / 'reviewed-reconstruction.json'
CHECKED_AT = '2026-10-09'
ENTRY = 'https://www.pref.fukushima.lg.jp/sec/11015e/kasokukahama.html'
DOCUMENTS = {2025: 'https://www.pref.fukushima.lg.jp/uploaded/attachment/683756.pdf',
             2026: 'https://www.pref.fukushima.lg.jp/uploaded/attachment/740195.pdf',
             'progress': 'https://www.pref.fukushima.lg.jp/uploaded/attachment/739654.pdf'}
ROW_ID = 'reconstruction-hamadori-prefecture-published-plan'


def path_for(url):
    return CACHE / (hashlib.sha256(url.encode()).hexdigest() + '.bin')


def fetch(url, policy_state, policy_revision):
    CACHE.mkdir(parents=True, exist_ok=True)
    receipt = dict(url=url, retrieved_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                   network_policy_state=policy_state, executor_policy_type='unrestricted')
    if policy_revision:
        receipt['network_spec_revision'] = policy_revision
    # Readiness must be observed separately before selecting --policy-state.
    try:
        with build_opener(CheckedRedirect()).open(Request(checked(url), headers={
                'User-Agent': 'japan-budget-tracker/0.1 (public official budget research)'}), timeout=25) as response:
            size = response.headers.get('Content-Length')
            if size and int(size) > MAX_BYTES:
                raise ValueError('File exceeds size limit')
            content = response.read(MAX_BYTES + 1)
            if len(content) > MAX_BYTES:
                raise ValueError('File exceeds size limit')
            receipt.update(status='downloaded', final_url=checked(response.geturl()), bytes=len(content),
                           sha256_original=hashlib.sha256(content).hexdigest(), content_type=response.headers.get_content_type())
            path_for(url).write_bytes(content)
    except Exception as error:
        receipt.update(status='blocked_or_failed', **failure_details(error))
    with (CACHE / 'manifest.jsonl').open('a') as stream:
        stream.write(json.dumps(receipt, ensure_ascii=False) + '\n')
    print(json.dumps({k: receipt[k] for k in ['url', 'status', 'failure_category', 'http_status'] if k in receipt}))


def receipt_for(url, historical=None):
    records = [json.loads(line) for line in (CACHE / 'manifest.jsonl').read_text().splitlines()]
    receipt = next(item for item in reversed(records) if item['url'] == url and item['status'] == 'downloaded')
    content = path_for(url).read_bytes()
    assert len(content) == receipt['bytes'] and hashlib.sha256(content).hexdigest() == receipt['sha256_original']
    if historical:
        assert historical['sha256_original'] == receipt['sha256_original'] and historical['bytes'] == receipt['bytes'], 'Original changed: manual review required'
    if OUTPUT.exists():
        saved = json.loads(OUTPUT.read_text())
        previous = next((item for item in saved['originals'] + saved['historical_original_rechecks'] if item['url'] == url), None)
        if previous:
            assert previous['sha256_original'] == receipt['sha256_original'] and previous['bytes'] == receipt['bytes'], 'Original changed: manual review required'
            # Enrich older saved receipts from their exact historical acquisition,
            # never from a later recheck's readiness state or timestamp.
            historical_acquisition = next((item for item in records if item['url'] == url
                and item['retrieved_at_utc'] == previous['retrieved_at_utc']
                and item.get('sha256_original') == previous['sha256_original']), {})
            receipt = dict(historical_acquisition, **previous)
    keys = ['url', 'final_url', 'retrieved_at_utc', 'sha256_original', 'bytes', 'content_type',
            'network_policy_state', 'executor_policy_type', 'network_spec_revision']
    return {k: receipt[k] for k in keys if k in receipt}


def pdf_text(url, mode='-layout'):
    receipt_for(url)
    return subprocess.check_output(['pdftotext', mode, str(path_for(url)), '-']).decode()


def compact(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))


def sanitized(page):
    # Drop staff names, telephone numbers and email while retaining table notes.
    before_notes, _, notes = page.partition('（注１）')
    before_contacts = re.split(r'都道県名', before_notes)[0]
    return before_contacts.strip() + ('\n（注１）' + notes.strip() if notes else '')


def build():
    baseline = json.loads(BASELINE.read_text())
    result = dict(schema_version=1, checked_at=CHECKED_AT, sources=[], originals=[], rows=[], research_notes=[],
                  data_quality_findings=[], plan_checks=[], historical_original_rechecks=[])
    entry_html = path_for(ENTRY).read_text()
    for year in [2025, 2026]:
        url = DOCUMENTS[year]
        assert url.split('www.pref.fukushima.lg.jp')[1] in entry_html
        assert f'令和{year - 2018}年度事業計画' in compact(entry_html)
        pages = pdf_text(url).split('\f')
        raw = pdf_text(url, '-raw')
        business, grant = (488636, 244318) if year == 2025 else (502374, 251187)
        detail_page = 5
        date = '2025-04-01' if year == 2025 else '2026-04-10'
        assert f'令和{year - 2018}年4月{1 if year == 2025 else 10}日' in compact(pages[0])
        assert f'令和{year - 2018}年度' in compact(pages[detail_page])
        table = sanitized(pages[detail_page])
        assert 'うち交付金交付額' in table and '今回申請する額' in compact(table)
        pair_pattern = rf'{business:,}\s+{grant:,}'
        # The A-1 line and separate total line agree; raw extraction independently
        # reproduces the same numeric pair without relying on layout whitespace.
        assert len(re.findall(pair_pattern, table)) == 2
        assert len(re.findall(pair_pattern, raw)) == 2
        assert grant * 2 == business
        annual_page = unicodedata.normalize('NFKC', pages[3])
        annual = re.search(rf'【令和{year - 2018}年度】\s*([\d,]+)\s*千円(.*?)(?=【|$)', annual_page, re.S)
        assert annual and int(annual[1].replace(',', '')) == business
        components = [int(value.replace(',', '')) for value in re.findall(r'([\d,]+)\s*千円', annual[2])]
        assert sum(components) == business and len(components) == 3
        source_id = f'reconstruction-fukushima-hamadori-plan-{year}'
        excerpt = '\n\n'.join([pages[0].strip(), sanitized(pages[1]), annual[0].strip(), table])
        assert not re.search(r'@|電話番号|担当者氏名', excerpt)
        result['sources'].append(dict(id=source_id, title=f'福島県 浜通り地域等産業発展環境整備事業 令和{year - 2018}年度公表事業計画',
            url=url, locator=f'PDF1・2・4・{detail_page + 1}頁、年度計画と様式1-4今回欄', kind='公表事業計画・実際の交付決定とは別',
            accessed=CHECKED_AT, published=None, document_date=date, document_date_basis='提出文書本文日付・ウェブ公表日未確定',
            retrieved_via='公式PDF原本取得、pdftotext -layout/-raw、A-1と合計及び年度内訳と国費率の独立検算',
            sha256_extracted_text=hashlib.sha256(excerpt.encode()).hexdigest(), excerpt=excerpt))
        result['originals'].append(dict(**receipt_for(url), source_ids=[source_id], verification=dict(status='matched', checked_at=CHECKED_AT,
            method='様式1-4の今回欄A-1と合計が一致、raw抽出も一致。年度別3事業額の和を交付対象事業費に再集計し国費率1/2で交付金額を検算。年度は本文で確認。',
            locator=result['sources'][-1]['locator'], row_ids=[ROW_ID], fields=[f'amount{year}'], values={ROW_ID: {f'amount{year}': grant / 1000}})))
        result['plan_checks'].append(dict(fiscal_year=year, business_cost_thousand_yen=business, published_plan_grant_thousand_yen=grant,
            annual_component_amounts_thousand_yen=components, annual_components_sum_thousand_yen=sum(components), grant_rate='1/2',
            table_pair_occurrences=2, raw_pair_occurrences=2, status='原本表・別表年度内訳・国費率一致',
            initial_application_status='通知前原申請額・採点・申請充足率未確認', source_ids=[source_id]))
    progress_url = DOCUMENTS['progress']
    assert progress_url.split('www.pref.fukushima.lg.jp')[1] in entry_html
    progress = pdf_text(progress_url).split('\f')
    expected = [(3, 287850, 231780), (4, 287850, 263158), (5, 249318, 209481), (6, 244318, 226825), (7, 0, 0)]
    assert '令和7年3月末時点' in compact(progress[0])
    summary_records = [(int(y), int(a.replace(',', '')), int(b.replace(',', ''))) for y, a, b in
        re.findall(r'R([3-7])\s+([\d,]+)\s+([\d,]+)', progress[0])]
    assert summary_records == expected
    assert '契約に加え、交付決定、協定等' in compact(progress[0])
    assert '基金の取崩額ではなく、契約額の国費相当額' in compact(progress[1])
    assert sum(a for _, a, _ in expected) == 1069336 and sum(b for _, _, b in expected) == 931244
    detail_records = [(int(y), int(a.replace(',', '')), int(b.replace(',', ''))) for y, a, b in
        re.findall(r'R([3-6])\s+([\d,]+)\s+([\d,]+)', progress[1])]
    assert detail_records[:4] == expected[:4]
    sid = 'reconstruction-fukushima-hamadori-contract-progress-2024'
    excerpt = '\n\n'.join(page.strip() for page in progress[:2])
    assert not re.search(r'@|電話番号|担当者氏名', excerpt)
    result['sources'].append(dict(id=sid, title='福島県 浜通り事業 令和6年度進捗状況（契約状況）報告', url=progress_url,
        locator='PDF1頁総括表・2頁年度別内訳と定義注記', kind='契約額国費相当・実支出とは別', accessed=CHECKED_AT, published=None,
        document_date='2025-03-31', document_date_basis='本文の令和7年3月末時点・ウェブ公表日未確定',
        retrieved_via='公式PDF原本取得、pdftotext -layout、総括と年度別内訳・合計照合',
        sha256_extracted_text=hashlib.sha256(excerpt.encode()).hexdigest(), excerpt=excerpt))
    result['originals'].append(dict(**receipt_for(progress_url), source_ids=[sid], verification=dict(status='downloaded_only', checked_at=CHECKED_AT,
        method='R3〜R7の交付額・契約済国費相当額、各合計、詳細表R3〜R6一致。2025/2026比較行ではない。', locator=result['sources'][-1]['locator'], row_ids=[], fields=[])))
    result['contract_progress_observations'] = [dict(fiscal_year=2018 + year, as_of='2025-03-31', recipient='福島県',
        grant_million_yen=grant / 1000, contracted_national_equivalent_million_yen=contract / 1000,
        status='原本総括と内訳照合済み・2025/2026同期間執行ではない', basis='契約・交付決定・協定等の国費相当額',
        is_cash_spending=False, source_ids=[sid]) for year, grant, contract in expected]
    result['originals'].append(dict(**receipt_for(ENTRY), source_ids=[], verification=dict(status='downloaded_only', checked_at=CHECKED_AT,
        method='公式県入口がR7/R8計画とR6進捗へのリンクを掲載。年度はリンク名とPDF本文を確認。', locator='HTML事業計画・進捗', row_ids=[], fields=[])))
    result['rows'].append(dict(id=ROW_ID, ministry='復興庁', program='福島再生加速化交付金（浜通り地域等産業発展環境整備事業）公表県事業計画',
        region='福島県', prefecture='福島県', basis='公表事業計画の今回欄（参考）', account='財源年度・会計区分未照合', unit='百万円',
        scope='県実施事業計画のうち交付金交付額・様式1-4今回欄', amount2025=244.318, amount2026=251.187,
        period2025='2025年度・2025年4月1日計画', period2026='2026年度・2026年4月10日計画',
        comparability='参考・計画期間と事業構成差', evidence_status='原本数値照合済み', geography_status='県事業・15市町村への配分未分解',
        source_ids=['reconstruction-fukushima-hamadori-plan-2025', 'reconstruction-fukushima-hamadori-plan-2026'],
        note='県が公表した事業計画の「うち交付金交付額」今回欄。表注記は「今回申請する額」と規定するが通知後提出計画であり、通知前原申請額・実際の交付決定・支出額とは扱わない。2025はR3〜R7、2026はR3〜R12の計画で、事業構成も異なるため参考比較。前回まで・計の累積額を当該年度額に混ぜず、復興庁第64/68/69回通知とも加算しない。対象15市町村の所在地を確認できても県額を各市町村へ配分しない。'))
    for original in baseline['originals']:
        if 'kanougaku.pdf' not in original['url']:
            continue
        receipt = receipt_for(original['url'], historical=original)
        result['historical_original_rechecks'].append(dict(**receipt, status='既存SHA-256・byte数一致',
            historical_retrieved_at_utc=original['retrieved_at_utc'], historical_evidence_preserved=True))
    result['interim_remainder_assessment'] = dict(status='丸め値の突合一致・排他性未確定', plan_fiscal_year=2026,
        published_plan_grant_thousand_yen=251187, notification_68_national_cost_million_yen=5, notification_69_national_cost_million_yen=246,
        candidate_sum_million_yen=251, plan_rounded_million_yen=251,
        reason='県R8計画の251,187千円は通知2回の国費丸め値5+246百万円と一致するが、暫定分・残額を分けた計画欄や交付決定記録はない。国費の排他性を認定せず、再掲事業費502百万円を合算しない。',
        source_ids=['reconstruction-fukushima-hamadori-plan-2026', 'reconstruction-fukushima-notification-68', 'reconstruction-fukushima-notification-69'])
    result['network_observation'] = dict(acquisition_states=[dict(url=o['url'], retrieved_at_utc=o['retrieved_at_utc'],
        **{k: o[k] for k in ['network_policy_state', 'executor_policy_type', 'network_spec_revision'] if k in o}) for o in result['originals']],
        assessment='各原本の取得時に観測した状態を保存。unknownを適用済みと認定せず、既存プロキシ・TLS検証を維持。後の取得成功やenforced観測を既存取得へ遡及しない。', successful_official_host='www.pref.fukushima.lg.jp')
    result['research_notes'].append(dict(ministry='復興庁', status='県原本取得・計画参考比較追加・通知排他性未確定', checked_at=CHECKED_AT,
        note='仕様3 unrestricted/unknown・executor unrestrictedの環境で、既存プロキシとTLS検証を維持し福島県R7/R8事業計画・R6契約進捗原本を取得。計画の国費244.318/251.187百万円を年度明示の今回欄・独立年度内訳・国費率で照合し参考比較。前回まで/計の累積額、通知回、実際の交付決定、支出とは別。第64/68/69回PDFは再取得して既存ハッシュ一致、127通知観測と年度未確認87件を保持。田村市38百万円不整合は未解消。',
        source_ids=[s['id'] for s in result['sources']],
        next_steps=['第68/69回別の国費・交付決定記録で暫定分と残額の排他性を照合。県年度計画との丸め一致だけで確定しない。',
                    '県進捗は契約・交付決定・協定等の国費相当額。2025/2026同期間支出の原本を別に取得。',
                    '通知前原申請額・審査記録と、田村市の対象範囲・訂正資料を確認。']))
    result['research_notes'].append(dict(ministry='復興庁', status='過年度契約進捗原本確認・2025/2026支出未収載', checked_at=CHECKED_AT,
        note='R6進捗原本の2025年3月末時点はR3〜R6交付額1,069.336百万円、契約済国費相当931.244百万円。R6年度分244.318/226.825百万円を総括と詳細で照合。原注記は契約に加え交付決定・協定等を含むと規定し実際の現金支出と異なる。R7の0は対象資料時点の記載であり2025年度年間ゼロと認定しない。', source_ids=[sid]))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--policy-state', choices=['unknown', 'enforced'], help='State actually observed for this acquisition; required with --fetch')
    parser.add_argument('--policy-revision', help='Optional spec revision actually observed for this acquisition')
    args = parser.parse_args()
    if args.fetch:
        if args.policy_state is None:
            parser.error('--fetch requires the observed --policy-state; never infer readiness')
        old = json.loads(BASELINE.read_text())
        urls = [ENTRY, *DOCUMENTS.values(), *[o['url'] for o in old['originals'] if 'kanougaku.pdf' in o['url']]]
        for url in urls:
            fetch(url, args.policy_state, args.policy_revision)
    result = build()
    if args.write:
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    else:
        expected = json.loads(OUTPUT.read_text())
        assert expected == result, 'Reviewed evidence differs from original re-extraction'
    print(json.dumps({k: len(result[k]) for k in ['sources', 'originals', 'rows', 'contract_progress_observations', 'historical_original_rechecks']}))


if __name__ == '__main__':
    main()
