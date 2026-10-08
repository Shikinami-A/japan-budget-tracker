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
}
MANIFEST = CACHE / 'cabinet-regional-manifest.jsonl'


def fetch_missing(receipts):
    # Only these three official government hosts are added, in this process.
    # This does not edit the shared fetcher or permit arbitrary redirects.
    fetch_sources.HOSTS.update({'www.chisou.go.jp', 'www.cas.go.jp', 'www.cao.go.jp'})
    opener = build_opener(fetch_sources.CheckedRedirect())
    for source_id, url in URLS.items():
        if receipts.get(source_id, {}).get('status') == 'downloaded':
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
            receipt.update(status='blocked_or_failed', error_type=type(error).__name__, http_status=error.code)
        except (URLError, TimeoutError, ValueError) as error:
            receipt.update(status='blocked_or_failed', error_type=type(error).__name__)
        with MANIFEST.open('a') as file:
            file.write(json.dumps(receipt, ensure_ascii=False) + '\n')
        receipts[source_id] = receipt
        print(json.dumps({'source_id': source_id, 'status': receipt['status'],
                          'http_status': receipt.get('http_status')}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    args = parser.parse_args()
    receipts = {r['source_id']: r for r in map(json.loads, MANIFEST.read_text().splitlines())}
    if args.fetch:
        fetch_missing(receipts)
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
    original = {k: receipt[k] for k in ('url', 'final_url', 'retrieved_at_utc', 'sha256_original', 'bytes', 'content_type')}
    original.update(source_id=source_id, download_status='downloaded',
                    verification={'status': 'downloaded_only', 'checked_at': '2026-10-09',
                                  'method': '行政レビューの制度・年度・執行表を確認。公開比較行との金額照合は対象なし。',
                                  'locator': source['locator'], 'row_ids': [], 'fields': []})
    report = {
        'schema_version': 1, 'checked_at': '2026-10-09', 'sources': [source], 'originals': [original], 'rows': [],
        'research_notes': [{
            'ministry': '内閣府', 'status': '比較可能な地域原本未確認', 'source_ids': [source_id],
            'note': '地域未来交付金は2025年度第1次補正から創設。取得済み2026年度行政レビューには2025年度執行額と2026年度当初・繰越予算が載るが、2026年度執行額は「--」。年度別地域配分・財源・同一採択回をそろえた比較は未成立。支出先一覧は一部を「その他」に集約し、他省庁への移替え・地方支分部局経由・直接交付の階層が混在するため重複加算しない。',
        }, {
            'ministry': '内閣官房', 'status': '公式原本取得失敗', 'source_ids': [],
            'note': '新地方創生・第2世代交付金2025年度採択と地域未来推進型2026年1月募集の公式採択資料候補を探索。chisou.go.jp掲載頁・PDFおよびCAS採択概要は今回の直接取得で404。検索抽出では補正と当初・複数決定日を含む資料が示されたため、制度改称と財源・回次を未確認のまま地域前年比を作らない。',
        }],
        'verified_source_facts': facts,
        'unadopted_discoveries': [
            {'url': URLS['cabinet-regional-2025-index'], 'evidence_status': 'Exa公式掲載頁抽出のみ・原本未取得',
             'finding': '2025年4月第1次R6補正、4月第2次R6補正＋R7当初、9月R6補正＋R7当初の別回次資料。'},
            {'url': URLS['cabinet-regional-2026-index'], 'evidence_status': 'Exa公式掲載頁抽出のみ・原本未取得',
             'finding': '2026年1月募集の採択結果。交付対象事業概要はR7当初・R7補正・R8当初を掲示。個別金額の財源分割を原本確認していない。'},
        ],
        'retrieval_attempts': [{k: r[k] for k in ('source_id', 'url', 'retrieved_at_utc', 'status', 'http_status', 'error_type') if k in r}
                               for r in receipts.values()],
        'search_provenance': {'provider': 'Exa', 'sources_reviewed_requested_results': 30,
                              'official_index_pages_fetched_as_extracted_text': 2,
                              'search_text_is_not_original': True},
    }
    (ROOT / 'data/reviewed-cabinet-regional.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'sources': len(report['sources']), 'originals': len(report['originals']), 'rows': 0,
                      'retrieval_attempts': len(report['retrieval_attempts']), 'facts': facts}, ensure_ascii=False))


if __name__ == '__main__':
    main()
