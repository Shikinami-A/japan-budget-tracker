"""Verify Fukushima acceleration-grant notification originals without mixing rounds."""
import argparse
import hashlib
import json
import re
import subprocess
import time
import unicodedata
from decimal import Decimal
from pathlib import Path
from urllib.request import Request, build_opener

from fetch_sources import CheckedRedirect, HOSTS, MAX_BYTES, checked

HOSTS.add('www.reconstruction.go.jp')
ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache' / 'originals'
OUTPUT = ROOT / 'data' / 'reviewed-reconstruction.json'
CHECKED_AT = '2026-10-09'
BASE = 'https://www.reconstruction.go.jp/files/user/topics/main-cat1/sub-cat1-17/'
DOCUMENTS = {
    64: BASE + '20250401_fukushimasaiseikasoku_No64_kanougaku.pdf',
    68: BASE + '20260401_fukushimasaiseikasoku_No68_kanougaku.pdf',
    69: BASE + '20260408_fukushimasaiseikasoku_No69_kanougaku.pdf',
    'overview': BASE + '20260408_kasokuka-gaiyou.pdf',
    'entry': 'https://www.reconstruction.go.jp/topics/cat-11/sub-cat1-17/',
}
SUPPLEMENTAL_DOCUMENTS = {
    'hamadori-entry': 'https://www.reconstruction.go.jp/topics/cat-11/cat-41/cat-42/20210401222635/',
    'implementation-rules': BASE + 'jisshiyoukou_hamadori_2026rev.pdf',
    'grant-rules': BASE + 'kouhuyoukou_hamadori_2023rev.pdf',
}
DETAILS = {
    64: {'published': '2025-04-01', 'summary': [(50557, 37810), (47728, 36181), (199, 199), (1668, 834), (1, 1), (489, 244), (472, 350)],
         'hamadori_body_page': 20, 'hamadori_table_page': 21, 'hamadori_values': [489, 244]},
    68: {'published': '2026-04-01', 'summary': [(5895, 127), (4157, 104), (1236, 18), (502, 5)],
         'hamadori_body_page': 11, 'hamadori_table_page': 12, 'hamadori_values': [502, 5]},
    69: {'published': '2026-04-08', 'summary': [(23307, 17110), (20110, 15534), (2693, 1328), (2, 2), (502, 246)],
         'hamadori_body_page': 16, 'hamadori_table_page': 17, 'hamadori_values': [502, 246]},
}


def original_path(url):
    return CACHE / (hashlib.sha256(url.encode()).hexdigest() + '.bin')


def fetch(url):
    if original_path(url).exists():
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    receipt = dict(url=url, retrieved_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    with build_opener(CheckedRedirect()).open(Request(checked(url), headers={
        'User-Agent': 'japan-budget-tracker/0.1 (public official budget research)'}), timeout=30) as response:
        declared_size = response.headers.get('Content-Length')
        if declared_size and int(declared_size) > MAX_BYTES:
            raise ValueError('File exceeds size limit')
        content = response.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError('File exceeds size limit')
        receipt.update(status='downloaded', bytes=len(content), sha256_original=hashlib.sha256(content).hexdigest(),
                       final_url=checked(response.geturl()), content_type=response.headers.get_content_type())
        original_path(url).write_bytes(content)
    with (CACHE / 'manifest.jsonl').open('a') as stream:
        stream.write(json.dumps(receipt, ensure_ascii=False) + '\n')


def receipt_for(url):
    receipts = [json.loads(line) for line in (CACHE / 'manifest.jsonl').read_text().splitlines()]
    receipt = next(r for r in reversed(receipts) if r['url'] == url and r['status'] == 'downloaded')
    content = original_path(url).read_bytes()
    assert len(content) == receipt['bytes']
    assert hashlib.sha256(content).hexdigest() == receipt['sha256_original']
    keys = ['url', 'final_url', 'retrieved_at_utc', 'sha256_original', 'bytes', 'content_type']
    # Retain the first reviewed receipt when the bytes have not changed. A later
    # successful fetch is a recheck, not a replacement of historical evidence.
    if OUTPUT.exists():
        previous = next((item for item in json.loads(OUTPUT.read_text())['originals'] if item['url'] == url), None)
        if previous:
            assert previous['sha256_original'] == receipt['sha256_original'], 'Reviewed original changed; manual review required'
            assert previous['bytes'] == receipt['bytes']
            return {k: previous[k] for k in keys}
    return {k: receipt[k] for k in keys}


def pages_for(url):
    receipt_for(url)
    path = original_path(url)
    subprocess.run(['pdftotext', '-layout', str(path), str(path.with_suffix('.txt'))], check=True)
    return path.with_suffix('.txt').read_text().split('\f')


def normalized(text):
    return unicodedata.normalize('NFKC', text)


def no_contacts(page):
    return page.split('本件連絡先')[0].strip()


def build_base():
    sources, originals, notifications = [], [], []
    for notification_round in [64, 68, 69]:
        detail = DETAILS[notification_round]
        url = DOCUMENTS[notification_round]
        pages = pages_for(url)
        summary = normalized(pages[0])
        pairs = re.findall(r'事業費\s*([\d,]+)百万円[、\s]+国費\s*([\d,]+)百万円', summary)
        amounts = [tuple(int(v.replace(',', '')) for v in pair) for pair in pairs]
        assert amounts == detail['summary'], (notification_round, amounts)
        assert f'第{notification_round}回' in summary
        year, month, day = map(int, detail['published'].split('-'))
        assert f'令和{year - 2018}年{month}月{day}日' in re.sub(r'\s+', '', summary)
        body = no_contacts(pages[detail['hamadori_body_page'] - 1])
        table = pages[detail['hamadori_table_page'] - 1]
        assert '浜通り地域等産業発展' in table and '交付可能額【国費】' in table
        table_match = re.search(r'福\s*島\s*県\s+([\d,]+)\s+([\d,]+)', normalized(table))
        assert table_match
        hamadori_values = [int(v.replace(',', '')) for v in table_match.groups()]
        assert hamadori_values == detail['hamadori_values']
        if notification_round == 68:
            compact_body = re.sub(r'\s+', '', normalized(body))
            assert '暫定予算期間中に必要な額' in compact_body
            assert '交付可能額の残額' in compact_body
            assert '令和8年度予算成立後' in compact_body
        source_id = f'reconstruction-fukushima-notification-{notification_round}'
        excerpt = '\n\n'.join([pages[0].strip(), body, table.strip()])
        sources.append(dict(id=source_id, title=f'福島再生加速化交付金 第{notification_round}回交付可能額通知（通知回単独・年度比較不可）',
                            url=url, locator=f'PDF1頁（通知全体の国費）、PDF{detail["hamadori_body_page"]}〜{detail["hamadori_table_page"]}頁（浜通り事業本文・福島県別紙）',
                            kind='交付可能額通知・比較条件調査', accessed=CHECKED_AT, published=detail['published'],
                            retrieved_via='公式PDF原本取得、pdftotext -layoutによる対象頁抽出',
                            sha256_extracted_text=hashlib.sha256(excerpt.encode()).hexdigest(), excerpt=excerpt))
        observation_id = f'reconstruction-notification-{notification_round}-hamadori-fukushima'
        notifications.append(dict(id=observation_id, fiscal_year=year, published=detail['published'], notification_round=notification_round,
                                  program='福島再生加速化交付金（浜通り地域等産業発展環境整備事業）', prefecture='福島県',
                                  recipient='福島県', basis='暫定予算期間の交付可能額' if notification_round == 68 else '当該通知回の交付可能額',
                                  business_cost_million_yen=hamadori_values[0], national_cost_million_yen=hamadori_values[1],
                                  unit='百万円', source_ids=[source_id], is_annual_total=False, comparison_status='比較条件未確定'))
        originals.append(dict(**receipt_for(url), source_ids=[source_id], verification=dict(
            status='downloaded_only', checked_at=CHECKED_AT, method='各通知回の表頭日付、総括の事業費・国費全組、浜通り事業福島県表の2欄を抽出し照合。公表比較行への金額照合はなく、年度比較額としては採用しない。',
            locator=sources[-1]['locator'], row_ids=[], observation_ids=[observation_id], fields=[])))
    overview = pages_for(DOCUMENTS['overview'])[0].strip()
    assert '第３期復興・創生期間' in overview
    sources.append(dict(id='reconstruction-fukushima-overview-2026', title='福島再生加速化交付金の概要（2026年版）',
                        url=DOCUMENTS['overview'], locator='PDF1頁、制度メニューと第3期復興・創生期間', kind='制度説明',
                        accessed=CHECKED_AT, published=None, document_date_inferred_from_filename='2026-04-08',
                        retrieved_via='公式PDF原本取得、pdftotext -layout',
                        sha256_extracted_text=hashlib.sha256(overview.encode()).hexdigest(), excerpt=overview))
    originals.append(dict(**receipt_for(DOCUMENTS['overview']), source_ids=['reconstruction-fukushima-overview-2026'],
                          verification=dict(status='downloaded_only', checked_at=CHECKED_AT, method='制度概要の第3期復興・創生期間とメニューを確認。資料本文に公表日なし。', locator='PDF1頁', row_ids=[], fields=[])))
    entry_html = original_path(DOCUMENTS['entry']).read_text()
    for key, url in DOCUMENTS.items():
        if key != 'entry':
            assert url in entry_html, url
    originals.append(dict(**receipt_for(DOCUMENTS['entry']), source_ids=[], verification=dict(
        status='downloaded_only', checked_at=CHECKED_AT, method='公式制度入口原本のハッシュと3通知回・制度概要PDFリンクを照合', locator='HTML', row_ids=[], fields=[])))
    return dict(schema_version=1, checked_at=CHECKED_AT, sources=sources, originals=originals, rows=[],
                research_notes=[dict(ministry='復興庁', status='原本取得済・通知段階差で未比較',
                                     note='福島再生加速化交付金の2025年4月第64回、2026年4月第68回・第69回と制度概要を原本取得。2026年第68回は暫定予算期間中の国費、第69回は成立後の通知であり、2025年初回と同条件の年度比較は未成立。浜通り事業費502百万円が第68・69回で重複し、通知回合算で年度額を作らない。',
                                     source_ids=[s['id'] for s in sources], checked_at=CHECKED_AT)],
                notification_observations=notifications,
                comparison_assessment=dict(status='同条件の年度比較未成立', public_comparison_rows_added=0,
                    reasons=[
                        '2025年4月1日第64回と2026年4月1日第68回は同条件でない。第68回は暫定予算期間中に必要な国費だけを通知している。',
                        '2026年4月8日第69回を第64回と直接比較すると暫定期間の通知国費が含まれるか不明。メニュー構成・対象団体数も異なる。',
                        '第64回全体には福島健康不安対策事業・水産業共同利用施設復興促進整備事業が含まれるが、第69回全体には含まれない。未掲載をゼロに置き換えない。',
                        '浜通り事業の暫定通知は成立後に残額通知予定と明記。ただし第69回本文には暫定額控除後の残額との明示なし。5+246=251百万円は追加照合が必要な候補計算で、確定年度額として公表しない。',
                        '浜通りの事業費502百万円は第68回・第69回の双方に掲載される。事業費の合算は重複計上になるため禁止。'],
                    next_steps=[
                        '2026年第68回・第69回の国費が排他的な暫定分・残額であることを県の交付決定資料・事業計画又は追加公式説明で照合する。',
                        '帰還・移住等環境整備は第64回・第68回・第69回の要素事業、年間計画、財源区分の対応を作成する。単に通知回全体を加算しない。',
                        '2025/2026の年度当初・補正・追加通知を分離した上で、同じ制度メニュー・受取団体・決定段階の比較を再判定する。']))


def recipient_table(pages, notification_round):
    page_indexes = {64: [3, 4, 5], 68: [2, 3], 69: [3, 4]}[notification_round]
    expected_count = {64: 44, 68: 39, 69: 41}[notification_round]
    result, totals = [], None
    for page_index in page_indexes:
        page = normalized(pages[page_index])
        expected_unit = '千円' if notification_round == 68 else '百万円'
        assert f'単位:{expected_unit}' in re.sub(r'\s+', '', page)
        # This union name is wrapped across the numeric line in all three PDFs.
        page = re.sub(r'福島地方水道\s*\n\s*([\d,.]+)\s+([\d,.]+)\s*\n\s*用水供給企業団',
                      r'福島地方水道用水供給企業団  \1  \2', page)
        for line in page.splitlines():
            match = re.match(r'^\s*(\S(?:.*?\S)?)\s{2,}([\d,.]+)\s+([\d,.]+)\s*$', line)
            if not match:
                continue
            name = re.sub(r'\s+', '', match[1])
            amounts = [Decimal(value.replace(',', '')) for value in match.groups()[1:]]
            if name == '計':
                assert totals is None
                totals = amounts
            else:
                assert name.endswith(('市', '町', '村', '県', '企業団')), name
                result.append(dict(recipient=name, amounts=amounts, pdf_page=page_index + 1))
    assert len(result) == expected_count and len({item['recipient'] for item in result}) == expected_count
    assert totals is not None
    differences = [sum(item['amounts'][field] for item in result) - totals[field] for field in [0, 1]]
    # The thousand-yen interim table sums exactly; the million-yen tables
    # explicitly warn that independently rounded rows need not sum to totals.
    if notification_round == 68:
        assert differences == [0, 0]
    else:
        assert all(abs(value) <= Decimal(expected_count) / 2 + Decimal('0.5') for value in differences)
    return result, totals, differences, page_indexes


def add_supplemental_source(result, key, source_id, title, indexes, published, assertions):
    url = SUPPLEMENTAL_DOCUMENTS[key]
    pages = pages_for(url)
    excerpt = '\n\n'.join(no_contacts(pages[index]) for index in indexes)
    compact = re.sub(r'\s+', '', normalized(excerpt))
    for assertion in assertions:
        assert assertion in compact, assertion
    locator = 'PDF' + '・'.join(str(index + 1) for index in indexes) + '頁'
    result['sources'].append(dict(id=source_id, title=title, url=url, locator=locator,
                                 kind='交付制度・配分条件', accessed=CHECKED_AT, published=None,
                                 document_date=published, document_date_basis='本文の一部改正日・インターネット公表日は未確認',
                                 retrieved_via='公式PDF原本取得、pdftotext -layoutによる対象頁抽出',
                                 sha256_extracted_text=hashlib.sha256(excerpt.encode()).hexdigest(), excerpt=excerpt))
    result['originals'].append(dict(**receipt_for(url), source_ids=[source_id], verification=dict(
        status='downloaded_only', checked_at=CHECKED_AT, method='配分条件・手続・改正日を原本本文で確認。申請額・実際の決定記録・執行額の実値は未収載。',
        locator=locator, row_ids=[], fields=[])))


def build():
    result = build_base()
    for observation in result['notification_observations']:
        notification_round = observation['notification_round']
        observation['fiscal_year_candidate'] = DETAILS[notification_round]['published'][:4]
        observation['fiscal_year_candidate'] = int(observation['fiscal_year_candidate'])
        observation['fiscal_year'] = 2026 if notification_round == 68 else None
        observation['fiscal_year_status'] = '本文に令和8年度暫定予算と明示' if notification_round == 68 else '通知額の年度欄未確認・公表日から確定しない'
        observation['numeric_verification'] = '原本表の数値照合済み'
    checks = []
    for notification_round in [64, 68, 69]:
        url = DOCUMENTS[notification_round]
        pages = pages_for(url)
        records, totals, differences, indexes = recipient_table(pages, notification_round)
        source_id = f'reconstruction-return-migration-recipients-{notification_round}'
        excerpt = '\n\n'.join(pages[index].strip() for index in indexes)
        locator = f'PDF{indexes[0] + 1}〜{indexes[-1] + 1}頁・帰還移住等市町村等別通知額'
        result['sources'].append(dict(id=source_id, title=f'帰還・移住等環境整備 第{notification_round}回通知の主体別交付可能額（年度比較未成立）',
                                     url=url, locator=locator, kind='通知回単独の地域配分・年度比較不可',
                                     accessed=CHECKED_AT, published=DETAILS[notification_round]['published'],
                                     retrieved_via='公式PDF原本取得、pdftotext -layoutによる対象頁抽出',
                                     sha256_extracted_text=hashlib.sha256(excerpt.encode()).hexdigest(), excerpt=excerpt))
        original = next(item for item in result['originals'] if item['url'] == url)
        original['source_ids'].append(source_id)
        factor = Decimal(1000) if notification_round == 68 else Decimal(1)
        for record in records:
            result['notification_observations'].append(dict(
                id=f'reconstruction-notification-{notification_round}-return-{record["recipient"]}',
                fiscal_year=2026 if notification_round == 68 else None,
                fiscal_year_candidate=int(DETAILS[notification_round]['published'][:4]),
                fiscal_year_status='本文に令和8年度暫定予算と明示' if notification_round == 68 else '通知額の年度欄未確認・公表日から確定しない',
                published=DETAILS[notification_round]['published'], notification_round=notification_round,
                program='福島再生加速化交付金（帰還・移住等環境整備）', prefecture='福島県',
                recipient=record['recipient'], basis='暫定予算期間の交付可能額' if notification_round == 68 else '当該通知回の交付可能額',
                business_cost_million_yen=float(record['amounts'][0] / factor),
                national_cost_million_yen=float(record['amounts'][1] / factor), unit='百万円', original_unit='千円' if notification_round == 68 else '百万円',
                source_ids=[source_id], pdf_page=record['pdf_page'], is_annual_total=False,
                comparison_status='同条件の年度比較未成立', numeric_verification='原本表の数値照合済み',
                municipality_mapping_status='自治体名を原本確認・選挙区対応未照合' if record['recipient'].endswith(('市', '町', '村')) else '県又は組合への配分・市町村へ再配分しない'))
        checks.append(dict(notification_round=notification_round, recipients=len(records), original_unit='千円' if notification_round == 68 else '百万円',
                           totals=[float(value) for value in totals], sum_minus_original_total=[float(value) for value in differences],
                           status='千円額の完全一致' if notification_round == 68 else '各行丸め注記の許容範囲内・総括と内訳を合算しない', source_ids=[source_id]))
        original['observation_verification'] = dict(status='原本表の数値照合済み・年度比較は未成立', checked_at=CHECKED_AT,
            locator=locator, observation_ids=[item['id'] for item in result['notification_observations'] if source_id in item['source_ids']],
            fields=['business_cost_million_yen', 'national_cost_million_yen'], source_ids=[source_id])
    result['recipient_table_checks'] = checks
    for notification_round, indexes in [(64, [6, 7]), (69, [5])]:
        url = DOCUMENTS[notification_round]
        pages = pages_for(url)
        source_id = f'reconstruction-return-migration-main-projects-{notification_round}'
        excerpt = '\n\n'.join(no_contacts(pages[index]) for index in indexes)
        compact = re.sub(r'\s+', '', normalized(excerpt))
        assertions = ['木材加工流通施設等整備事業田村地区', '1,367百万円', '飯舘村産業団地整備事業深谷地区'] if notification_round == 64 else ['飯舘村産業団地整備事業深谷地区(基金型)《新規》', '3,634百万円(2,726百万円)']
        for assertion in assertions:
            assert assertion in compact, assertion
        locator = 'PDF' + '・'.join(str(index + 1) for index in indexes) + '頁・主な事業（抜粋であり全事業一覧ではない）'
        result['sources'].append(dict(id=source_id, title=f'第{notification_round}回 帰還・移住等環境整備の主な事業（事業構成・資料内部整合の調査）',
            url=url, locator=locator, kind='主な事業の抜粋・年度増減原因未確定', accessed=CHECKED_AT,
            published=DETAILS[notification_round]['published'], retrieved_via='公式PDF原本取得、pdftotext -layoutによる対象頁抽出',
            sha256_extracted_text=hashlib.sha256(excerpt.encode()).hexdigest(), excerpt=excerpt))
        next(item for item in result['originals'] if item['url'] == url)['source_ids'].append(source_id)
    tamura = next(item for item in result['notification_observations'] if item['notification_round'] == 64 and item['recipient'] == '田村市')
    assert tamura['national_cost_million_yen'] == 1329
    tamura['internal_consistency_status'] = '主な事業の単独国費が市総額を上回る・対象範囲又は原資料の数値不一致未解消'
    result['data_quality_findings'] = [dict(id='reconstruction-64-tamura-internal-mismatch', ministry='復興庁', recipient='田村市',
        status='原本内の対象範囲又は数値不一致未解消', notification_round=64, recipient_total_national_cost_million_yen=1329,
        main_project_national_cost_million_yen=1367, main_project='木材加工流通施設等整備事業 田村地区', difference_million_yen=38,
        numeric_verification='原本本文の抽出に一致・PDF4頁及び7頁の画像も確認',
        inference_limit='両表を同じ対象範囲と仮定すると単独事業が総額を上回る。原因は未確認で、誤植や対象年度差と決めつけず原表値を補正しない。',
        source_ids=['reconstruction-return-migration-recipients-64', 'reconstruction-return-migration-main-projects-64'])]
    result['official_explanations'] = [dict(recipient='飯舘村', status='事業構成を原本確認・年度増減原因未確定',
        explanation='第69回では深谷地区産業団地の基金型3,634百万円（国費2,726百万円）が新規として掲載。別に深谷地区421百万円（国費316百万円）、小宮地区道路131百万円（国費105百万円）も掲載。第64回は深谷地区901百万円（国費675百万円）が主な事業。新規掲載は公式根拠で確認できるが、申請充足率・実際の進捗・暫定国費との関係は未確認で年度増額の因果説明は確定しない。',
        source_ids=['reconstruction-return-migration-main-projects-64', 'reconstruction-return-migration-main-projects-69']),
        dict(recipient='大熊町', status='事業構成を原本確認・年度増減原因未確定',
        explanation='第64回は西大和久地区復興拠点整備3,233百万円（国費2,425百万円）が新規で掲載。第69回主な事業は下水道78百万円（国費58百万円）、水利施設777百万円（国費586百万円）、産業交流施設540百万円（国費405百万円）。掲載事業の構成差を確認したが、旧事業の完了・廃止・不採択とは認定しない。',
        source_ids=['reconstruction-return-migration-main-projects-64', 'reconstruction-return-migration-main-projects-69'])]
    add_supplemental_source(result, 'implementation-rules', 'reconstruction-hamadori-implementation-rules-2026',
                            '浜通り地域等産業発展環境整備事業 実施要綱（2026年4月施行）', [1, 2, 3], '2026-03-03',
                            ['必要性、効率性、事業実施の確実性及び進捗状況等', '予算の範囲内で配分計画', '令和8年4月1日から施行する'])
    add_supplemental_source(result, 'grant-rules', 'reconstruction-hamadori-grant-rules-2023',
                            '浜通り地域等産業発展環境整備事業 交付要綱（2023年改正）', [0, 1, 2], '2023-03-27',
                            ['交付の申請があった場合', 'その内容を審査', '交付の決定', '実績報告'])
    entry_url = SUPPLEMENTAL_DOCUMENTS['hamadori-entry']
    entry_html = original_path(entry_url).read_text()
    for key in ['implementation-rules', 'grant-rules']:
        assert SUPPLEMENTAL_DOCUMENTS[key] in entry_html
    result['originals'].append(dict(**receipt_for(entry_url), source_ids=[], verification=dict(status='downloaded_only', checked_at=CHECKED_AT,
        method='現行浜通り制度入口と実施要綱・交付要綱リンクを照合', locator='HTML', row_ids=[], fields=[])))
    receipts = [json.loads(line) for line in (CACHE / 'manifest.jsonl').read_text().splitlines()]
    result['original_rechecks'] = [dict(**next(receipt for receipt in reversed(receipts) if receipt['url'] == url and receipt['status'] == 'downloaded'),
                                      checked_at=CHECKED_AT, assessment='既存照合原本とSHA-256・byte数一致、既存取得日時保持') for url in DOCUMENTS.values()]
    result['decision_evidence'] = dict(
        allocation_criteria=dict(status='公式要綱で確認', details='福島県の事業計画を受け、予算の範囲内で必要性・効率性・実施の確実性・進捗等を勘案。交付対象事業費の1/2を基準とする。', source_ids=['reconstruction-hamadori-implementation-rules-2026']),
        decision_stage=dict(status='手続を公式確認・実際の審査記録未取得', details='交付可能額通知、交付申請、審査、交付決定、支出・実績報告を別段階として扱う。通知は執行額ではない。', source_ids=['reconstruction-hamadori-grant-rules-2023']),
        application_amount=dict(status='未収載', value=None, reason='通知後修正計画と修正前申請額を分ける必要がある。実施要綱は通知後修正・公表を規定しており、公表計画額だけで申請額を確定しない。'),
        interim_remainder=dict(status='未確定', value=None, reason='第68回は残額を成立後通知予定とするが第69回に暫定額控除後の残額との明示なし。国費5+246は候補計算に留め、再掲事業費502は合算しない。'),
        same_period_execution=dict(status='未収載', value=None, reason='通知原本・要綱は支出実値を掲載しない。県の計画・進捗資料は原本未取得で、2025/2026同期間執行は未確認。'),
        decision_time_actors=dict(status='未照合', details='実際の配分決定時点の議員・首長・支持関係・働きかけ記録は未照合。要綱の法定権限から個人の関与を認定しない。'),
        required_official_host=dict(host='www.pref.fukushima.lg.jp', status='現在の通信許可外・接続を試みず', candidate_url='https://www.pref.fukushima.lg.jp/sec/11015e/kasokukahama.html',
                                   reason='現行掲載先候補。県の2025/2026事業計画・過年度進捗を原本取得して通知回別の前回まで/今回/計を確認する必要がある。検索抽出を原本に読み替えない。'))
    result['comparison_assessment']['reasons'].append('第64回・第69回は当該通知額の年度欄を原本で確認できず、公表日からの年度推定を確定値としない。第68回のみ本文が令和8年度暫定予算と明示。')
    result['comparison_assessment']['next_steps'][0] = 'www.pref.fukushima.lg.jpの通信許可追加後、2025/2026浜通り事業計画の前回まで・今回・計、実際の交付決定記録を照合し、第68/69回国費の排他性を確認する。'
    result['research_notes'][0]['note'] = '第64/68/69回通知原本を再取得し既存SHA-256一致を確認、既存取得日時を保持。帰還・移住等の44/39/41主体を構造化して数値照合したが年度比較未成立。第68回は暫定予算、第69回は成立後通知で事業費再掲。通知額の年度欄未確認はnull。浜通りの必要性・効率性・進捗等の配分条件は要綱確認、申請額・審査記録・同期間執行・決定時点の関係者は未確認。県の計画原本には通信許可追加が必要。'
    result['research_notes'][0]['source_ids'] = [source['id'] for source in result['sources']]
    result['research_notes'][0]['next_steps'] = result['comparison_assessment']['next_steps']
    result['research_notes'].append(dict(ministry='復興庁', status='事業構成確認・原本内不一致未解消', checked_at=CHECKED_AT,
        note='第69回飯舘村の深谷地区産業団地は基金型3,634百万円（国費2,726百万円）を新規掲載。大熊町も第64回の復興拠点新規事業と第69回の下水道・水利・産業交流施設で事業構成が異なるが、年度増減原因は未確定。第64回田村市は市別表の国費1,329百万円に対し主な木材施設事業だけで国費1,367百万円となり、原本4頁/7頁の画像でも38百万円差を確認。原本転記一致と資料内部整合を区別し、この不一致を未解消のまま原因分析に使わない。',
        source_ids=['reconstruction-return-migration-recipients-64', 'reconstruction-return-migration-main-projects-64', 'reconstruction-return-migration-main-projects-69'],
        next_steps=['田村市の申請書・実施箇所一覧・訂正記録で両欄の対象範囲及び国費差を再照合する。', '産業団地等の工程・需要・申請額・採択記録を取得し、新規掲載と年度増減の因果関係を別に判定する。']))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    if args.fetch:
        for url in [*DOCUMENTS.values(), *SUPPLEMENTAL_DOCUMENTS.values()]:
            fetch(url)
    result = build()
    if args.write:
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    else:
        assert json.loads(OUTPUT.read_text()) == result, 'Reviewed research differs from original re-extraction'
    print(json.dumps({'sources': len(result['sources']), 'originals': len(result['originals']),
                      'notification_observations': len(result['notification_observations']),
                      'comparison_rows': len(result['rows']), 'status': result['comparison_assessment']['status']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
