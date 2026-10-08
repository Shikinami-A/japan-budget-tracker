"""Record why Cabinet regional grants cannot yet form comparable annual rows.

Reads official cached PDF bytes. Optional --fetch retries only fixed official URLs,
using the existing proxy, verified TLS and reviewed redirects. Exa discoveries
are kept separate from original evidence and never become comparison amounts.
"""
import argparse
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

import fetch_sources

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/originals'
URLS = {
    'cabinet-regional-2025-index': 'https://www.chisou.go.jp/sousei/about/shinchihoukouhukin/dai2sedai/index.html',
    'cabinet-regional-2026-index': 'https://www.chisou.go.jp/sousei/about/chiikimiraikoufukin/suishin/index.html',
    'cabinet-regional-2025-april': 'https://www.chisou.go.jp/sousei/about/shinchihoukouhukin/dai2sedai/pdf/r7-dai2sedai_hosei2_tosyo.pdf',
    'cabinet-regional-2026-april': 'https://www.chisou.go.jp/sousei/about/chiikimiraikoufukin/suishin/pdf/r8_suishin_1.pdf',
    'cabinet-regional-2025-summary': 'https://www.chisou.go.jp/sousei/about/shinchihoukouhukin/pdf/saitaku_hosei2_tosyo_r7.pdf',
    'cabinet-regional-2025-national-summary': 'https://www.cas.go.jp/jp/seisaku/atarashii_chihousousei/honbukaigi/dai3/siryou1-1.pdf',
    'cabinet-regional-2025-second-summary': 'https://www.cas.go.jp/jp/seisaku/atarashii_chihousousei/honbukaigi/dai5/siryou2.pdf',
    'cabinet-regional-2026-review': 'https://www.cao.go.jp/yosan/pdf/r8/020891_kokai.pdf',
    'cabinet-regional-root': 'https://www.chisou.go.jp/sousei/index.html',
    # Current official entrypoints and search-discovered URLs. A 404 does not
    # establish a relocation or the absence of a published document.
    'cabinet-cao-policy-index': 'https://www.cao.go.jp/seisaku/seisaku.html',
    'cabinet-cao-current-root': 'https://www.cao.go.jp/',
    'cabinet-chisou-current-root': 'https://www.chisou.go.jp/',
    'cabinet-chisou-current-tiiki': 'https://www.chisou.go.jp/tiiki/index.html',
    'cabinet-cas-current-root': 'https://www.cas.go.jp/',
    'cabinet-cas-chiikimirai-index': 'https://www.cas.go.jp/jp/seisaku/chiikimirai/index.html',
    'cabinet-regional-2025-first-summary': 'https://www.chisou.go.jp/sousei/about/shinchihoukouhukin/pdf/saitaku_hosei_r7.pdf',
    'cabinet-regional-2025-september': 'https://www.chisou.go.jp/sousei/about/shinchihoukouhukin/dai2sedai/pdf/r7-2-dai2sedai.pdf',
    'cabinet-regional-2026-program-root': 'https://www.chisou.go.jp/sousei/about/chiikimiraikoufukin/index.html',
    'cabinet-regional-2026-criteria': 'https://www.chisou.go.jp/tiiki/kankyo/pdf/02_kouhukingaiyou26.pdf',
    'cabinet-regional-2026-explanation': 'https://www.chisou.go.jp/sousei/meeting/tihousousei_setumeikai/pdf/r08-01-20-shiryou04.pdf',
    'cabinet-regional-2026-application': 'https://www.chisou.go.jp/tiiki/tiikisaisei/kouhyou/260119/nintei.html',
    'cabinet-regional-2026-ordinance': 'https://www.chisou.go.jp/sousei/about/chiikimiraikoufukin/pdf/chiikimiraikoufukin_seidoyoukou.pdf',
    'cabinet-regional-2026-outline': 'https://www.chisou.go.jp/sousei/about/chiikimiraikoufukin/pdf/chiikimiraikoufukin_gaiyou.pdf',
}
MANIFEST = CACHE / 'cabinet-regional-manifest.jsonl'


def fetch_missing(receipts):
    # Only these three official government hosts are added, in this process.
    # This does not edit the shared fetcher or permit arbitrary redirects.
    fetch_sources.HOSTS.update({'www.chisou.go.jp', 'www.cas.go.jp', 'www.cao.go.jp'})
    opener = build_opener(fetch_sources.CheckedRedirect())
    CACHE.mkdir(parents=True, exist_ok=True)
    for source_id, url in URLS.items():
        cached = CACHE / (hashlib.sha256(url.encode()).hexdigest() + '.bin')
        if receipts.get(source_id, {}).get('status') == 'downloaded' and cached.exists():
            continue
        fetch_sources.checked(url)
        receipt = {'source_id': source_id, 'url': url,
                   'retrieved_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
        try:
            req = Request(url, headers={'User-Agent': 'japan-budget-tracker/0.1 (public official budget research)'})
            with opener.open(req, timeout=25) as response:
                content = response.read(fetch_sources.MAX_BYTES + 1)
                if len(content) > fetch_sources.MAX_BYTES:
                    raise ValueError('File exceeds size limit')
                receipt.update(status='downloaded', bytes=len(content),
                               sha256_original=hashlib.sha256(content).hexdigest(),
                               final_url=fetch_sources.checked(response.geturl()),
                               content_type=response.headers.get_content_type())
                (CACHE / (hashlib.sha256(url.encode()).hexdigest() + '.bin')).write_bytes(content)
        except HTTPError as error:
            receipt.update(status='blocked_or_failed', error_type=type(error).__name__, **fetch_sources.failure_details(error))
        except (URLError, TimeoutError, ValueError) as error:
            receipt.update(status='blocked_or_failed', error_type=type(error).__name__, **fetch_sources.failure_details(error))
        with MANIFEST.open('a') as file:
            file.write(json.dumps(receipt, ensure_ascii=False) + '\n')
        receipts[source_id] = receipt
        print(json.dumps({'source_id': source_id, 'status': receipt['status'],
                          'http_status': receipt.get('http_status')}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    args = parser.parse_args()
    target = ROOT / 'data/reviewed-cabinet-regional.json'
    previous = json.loads(target.read_text()) if target.exists() else {}
    cached_attempts = list(map(json.loads, MANIFEST.read_text().splitlines())) if MANIFEST.exists() else []
    receipts = {r['source_id']: r for r in cached_attempts}
    if args.fetch:
        fetch_missing(receipts)
    if not receipts:
        raise ValueError('Original cache is unavailable; use --fetch to obtain official originals')
    source_id = 'cabinet-regional-2026-review'
    receipt = receipts[source_id]
    if receipt['status'] != 'downloaded':
        raise ValueError('Administrative review original is unavailable')
    pdf = CACHE / (hashlib.sha256(receipt['url'].encode()).hexdigest() + '.bin')
    content = pdf.read_bytes()
    if len(content) != receipt['bytes'] or hashlib.sha256(content).hexdigest() != receipt['sha256_original']:
        raise ValueError('Cached original digest differs')
    text = subprocess.check_output(['pdftotext', '-layout', str(pdf), '-']).decode()
    pages = text.split('\f')
    normalized = lambda value: ''.join(value.split())
    if '令和７年度第１次補正予算より、地域未来交付金' not in normalized(pages[0]):
        raise ValueError('Program creation/funding statement differs')
    budget_page = pages[2]
    year_headers = re.search(r'予算額執行額表\s+2024\s+2025\s+2026\s+2027', budget_page)
    if not year_headers:
        raise ValueError('Budget-year column topology differs')
    execution = re.search(r'執行額\s+(--)\s+([\d,]+)\s+(--)\s+(--)', budget_page)
    initial = re.search(r'当初予算\s+(--)\s+([\d,]+)\s+([\d,]+)\s+(--)', budget_page)
    if not execution or not initial:
        raise ValueError('Budget/execution rows differ')
    facts = {
        'initial_budget2025_thousand_yen': int(initial[2].replace(',', '')),
        'initial_budget2026_thousand_yen': int(initial[3].replace(',', '')),
        'execution2025_thousand_yen': int(execution[2].replace(',', '')),
        'execution2026_printed': execution[3],
        'execution2026_value': None,
        'regional_annual_comparison_validated': False,
    }
    if facts['execution2025_thousand_yen'] != 183150017 or facts['execution2026_printed'] != '--':
        raise ValueError('Execution amounts differ')
    review_page = normalized(pages[14])
    for statement in ('次年度の継続事業に係る交付金申請', 'KPIの達成状況や効果検証の結果',
                      '採択基準の一つに「自立性」', '事業の内容や経費の妥当性、KPIの設定状況等を確認'):
        if statement not in review_page:
            raise ValueError('Administrative-review criteria statement differs')
    criteria_id = 'cabinet-regional-criteria-2026'
    facts['selection_and_progress_evidence'] = {
        'source_id': criteria_id, 'locator': 'PDF p.15 事業所管部局による点検・改善',
        'status': '所管部局の制度運用説明を原本確認・個別採択判断未照合',
        'criteria': ['事業内容・経費の妥当性', 'KPIの設定状況', '自立性'],
        'continuing_application': '過年度のKPI達成状況・効果検証の結果を踏まえた申請',
        'individual_requested_amounts': None, 'individual_scoring_and_decision_records': None,
        'political_influence_validated': False,
    }
    excerpt = ('PDF p.1: ' + normalized(pages[0][pages[0].index('令和７年度第１次補正'):])[:600]
               + '\nPDF p.3:\n' + budget_page[:1800]
               + '\nPDF p.17:\n' + pages[16][:2200])
    source = {
        'id': source_id, 'title': '2026年度内閣府行政事業レビュー：地域未来交付金（地域年次比較には未採用）',
        'url': receipt['url'], 'locator': 'PDF p.1制度創設時点、p.3年度別予算・執行、p.17資金の流れ、pp.23-27一部支出先',
        'kind': '行政事業レビュー', 'accessed': '2026-10-09', 'published': None,
        'document_date': '2026年度', 'document_date_precision': 'fiscal_year',
        'retrieved_via': '公式PDF原本をTLS検証付きHTTPSで取得、pdftotext -layoutで対象箇所確認',
        'sha256_extracted_text': hashlib.sha256(excerpt.encode()).hexdigest(), 'excerpt': excerpt,
    }
    criteria_excerpt = 'PDF p.15（所管部局による制度運用説明・個別判断ではない）:\n' + pages[14][:4800]
    criteria_source = dict(source, id=criteria_id,
                           title='2026年度内閣府行政事業レビュー：地域未来交付金の採択基準・進捗検証説明',
                           locator='PDF p.15 事業所管部局による点検・改善',
                           excerpt=criteria_excerpt,
                           sha256_extracted_text=hashlib.sha256(criteria_excerpt.encode()).hexdigest())
    original = {k: receipt[k] for k in ('url', 'final_url', 'retrieved_at_utc', 'sha256_original', 'bytes', 'content_type')}
    original.update(source_id=source_id, source_ids=[source_id, criteria_id], download_status='downloaded',
                    verification={'status': 'downloaded_only', 'checked_at': '2026-10-09',
                                  'method': '行政レビューの制度・年度・執行表、所管部局による採択基準・継続申請時の進捗検証説明を確認。公開比較行との金額照合は対象なし。',
                                  'locator': source['locator'] + '、p.15制度運用説明', 'row_ids': [], 'fields': []})
    # Preserve the previous reviewed retrieval date for identical bytes.
    # Subsequent downloads have their own history entries below.
    prior_originals = {r['source_id']: r for r in previous.get('originals', [])}
    if prior_originals.get(source_id, {}).get('sha256_original') == original['sha256_original']:
        original['retrieved_at_utc'] = prior_originals[source_id]['retrieved_at_utc']
    policy_id = 'cabinet-cao-policy-index'
    policy_receipt = receipts.get(policy_id)
    policy_source = policy_original = None
    if policy_receipt and policy_receipt['status'] == 'downloaded':
        policy_bytes = (CACHE / (hashlib.sha256(policy_receipt['url'].encode()).hexdigest() + '.bin')).read_bytes()
        if hashlib.sha256(policy_bytes).hexdigest() != policy_receipt['sha256_original'] or len(policy_bytes) != policy_receipt['bytes']:
            raise ValueError('Cached policy-index digest differs')
        if 'href="https://www.chisou.go.jp/sousei/index.html"' not in policy_bytes.decode('utf-8'):
            raise ValueError('Current CAO official regional-policy link differs')
        policy_excerpt = '内閣府「内閣府の政策」地方創生欄\n地方創生（内閣官房・内閣府 総合サイト）\nhttps://www.chisou.go.jp/sousei/index.html\n取得時点の公式入口は従来の総合サイトURLを案内。リンク先のHTTP 404は未解消で、移転・非公表と断定しない。'
        policy_source = {'id': policy_id, 'title': '内閣府の政策：現行地方創生入口の確認',
                         'url': policy_receipt['url'], 'locator': 'HTML #bunken 地方創生',
                         'kind': '公式掲載頁', 'accessed': '2026-10-09', 'published': None,
                         'retrieved_via': '公式HTML原本をTLS検証付きHTTPSで取得、リンク属性を確認',
                         'sha256_extracted_text': hashlib.sha256(policy_excerpt.encode()).hexdigest(), 'excerpt': policy_excerpt}
        policy_original = {k: policy_receipt[k] for k in ('url', 'final_url', 'retrieved_at_utc', 'sha256_original', 'bytes', 'content_type')}
        policy_original.update(source_id=policy_id, download_status='downloaded',
                               verification={'status': 'downloaded_only', 'checked_at': '2026-10-09',
                                             'method': '公式政策入口から案内される総合サイトURLを原本確認。地域金額は非掲載。',
                                             'locator': policy_source['locator'], 'row_ids': [], 'fields': []})
        if prior_originals.get(policy_id, {}).get('sha256_original') == policy_original['sha256_original']:
            policy_original['retrieved_at_utc'] = prior_originals[policy_id]['retrieved_at_utc']
    all_attempts = previous.get('retrieval_attempts', []) + list(map(json.loads, MANIFEST.read_text().splitlines()))
    attempt_fields = ('source_id', 'url', 'retrieved_at_utc', 'status', 'http_status', 'error_type', 'failure_category', 'sha256_original', 'bytes')
    history = []
    seen = set()
    for attempt in all_attempts:
        identity = (attempt['source_id'], attempt['url'], attempt['retrieved_at_utc'])
        if identity not in seen:
            seen.add(identity)
            record = {k: attempt[k] for k in attempt_fields if k in attempt}
            if record.get('error_type') == 'HTTPError' and 'http_status' in record:
                record.setdefault('failure_category', 'http_error')
            history.append(record)
    report = {
        'schema_version': 1, 'checked_at': '2026-10-09',
        'sources': [source, criteria_source] + ([policy_source] if policy_source else []),
        'originals': [original] + ([policy_original] if policy_original else []), 'rows': [],
        'research_notes': [{
            'ministry': '内閣府', 'status': '比較可能な地域原本未確認', 'source_ids': [source_id, criteria_id],
            'note': '地域未来交付金は2025年度第1次補正から創設。取得済み2026年度行政レビューには2025年度執行額と2026年度当初・繰越予算が載るが、2026年度執行額は「--」。年度別地域配分・財源・同一採択回をそろえた比較は未成立。支出先一覧は一部を「その他」に集約し、他省庁への移替え・地方支分部局経由・直接交付の階層が混在するため重複加算しない。p.15の所管部局説明で経費妥当性・KPI・自立性、継続申請時の進捗検証を確認したが、個別申請額・採点・決定記録の照合ではなく、政治的働きかけの証拠ではない。',
        }, {
            'ministry': '内閣官房', 'status': '公式原本取得失敗', 'source_ids': [policy_id] if policy_source else [],
            'note': '採択候補8URLを再取得してHTTP 404が継続。現行内閣府政策入口の原本は従来のchisou.go.jp/sousei/index.htmlを案内しており、別の掲載先への移転は未確認。chisou・CASの現行入口と検索で発見した追加採択・制度要綱・基準・申請資料もHTTP 404で取得失敗。CONNECT拒否とは区別し、非掲載・非公表と断定しない。検索抽出は2025年4月のR6補正第1次・第2次＋R7当初、9月回と、2026年1月募集のR7当初・補正・R8当初混在を示すため、原本の財源分割・回次未照合のまま地域前年比を作らない。',
        }],
        'verified_source_facts': facts,
        'unadopted_discoveries': [
            {'url': URLS['cabinet-regional-2025-index'], 'evidence_status': 'Exa公式掲載頁抽出のみ・原本未取得',
             'finding': '2025年4月第1次R6補正、4月第2次R6補正＋R7当初、9月R6補正＋R7当初の別回次資料。'},
            {'url': URLS['cabinet-regional-2026-index'], 'evidence_status': 'Exa公式掲載頁抽出のみ・原本未取得',
             'finding': '2026年1月募集の採択結果。交付対象事業概要はR7当初・R7補正・R8当初を掲示。個別金額の財源分割を原本確認していない。'},
            {'url': URLS['cabinet-regional-2025-first-summary'], 'evidence_status': 'Exa公式掲載頁抽出のみ・原本HTTP 404',
             'finding': '2025年4月1日のR6補正第1次採択概要。4月9日の第2次R6補正＋R7当初と財源・回次が違う。原本金額は未照合。'},
            {'url': URLS['cabinet-regional-2025-september'], 'evidence_status': 'Exa公式掲載頁抽出のみ・原本HTTP 404',
             'finding': '2025年9月2日公表・9月11日交付決定予定分。年度全体・4月回と混同せず、原本の重複と財源分割は未照合。'},
            {'url': URLS['cabinet-regional-2026-ordinance'], 'evidence_status': 'Exa公式掲載頁抽出のみ・原本HTTP 404',
             'finding': '2026年2月4日制度要綱候補。前年度以前の歳出予算に係る旧要綱の経過措置と省庁移替えを示す抽出。原本条文未照合。'},
            {'url': URLS['cabinet-regional-2026-criteria'], 'evidence_status': 'Exa公式掲載頁抽出のみ・原本HTTP 404',
             'finding': '2026年1月募集の優先採択テーマ・制度概要候補。採点・個別申請額・採否判断の記録ではない。'},
            {'url': URLS['cabinet-regional-2026-application'], 'evidence_status': 'Exa公式掲載頁抽出のみ・原本HTTP 404',
             'finding': '第76回地域再生計画の申請受付・旧第2世代交付金の読み替え候補。地域再生計画認定と交付金額の採択は別で、原本未照合。'},
        ],
        'retrieval_attempts': history,
        'search_provenance': {'provider': 'Exa', 'sources_reviewed_requested_results': 30,
                              'official_index_pages_fetched_as_extracted_text': 2,
                              'search_text_is_not_original': True,
                              'continuation_searches': {'checked_at': '2026-10-09', 'search_calls': 3,
                                                        'sources_reviewed_requested_results': 18,
                                                        'original_amounts_adopted_from_search': 0}},
    }
    (ROOT / 'data/reviewed-cabinet-regional.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'sources': len(report['sources']), 'originals': len(report['originals']), 'rows': 0,
                      'retrieval_attempts': len(report['retrieval_attempts']), 'facts': facts}, ensure_ascii=False))


if __name__ == '__main__':
    main()
