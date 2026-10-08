"""Review three large national budget changes without changing existing amounts.

--fetch uses the existing official-host allowlist, proxy and verified TLS.
Failures are bounded records and do not stop other sources. Without --fetch,
only hash-checked ignored originals and committed receipts are used. Normal
dashboard builds use the reviewed JSON and require neither cache nor network.
"""
import argparse
from collections import Counter
from decimal import Decimal
import csv
import hashlib
import io
import json
import re
import time
import unicodedata
import zipfile

from fetch_sources import MAX_BYTES, failure_details
from verify_mlit_originals import ROOT, fetch, original_path, pdf_pages, receipt_for
from verify_regional_followup import text_html

OUT = ROOT / 'data/reviewed-national-drivers.json'
FETCH_STATE = ROOT / '.cache/national-drivers-fetch.json'
CHECKED_AT = '2026-10-09'
URLS = {
    'national-mext-meal-index': 'https://www.mext.go.jp/a_menu/sports/syokuiku/kyu-lighten.html',
    'national-mext-budget-2026': 'https://www.mext.go.jp/content/20260407-ope_dev02-000044426_1.pdf',
    'national-mext-joint-policy': 'https://www.mext.go.jp/content/20251224-mxt_soseisk01-000046460_1.pdf',
    'national-tourism-budget-index': 'https://www.mlit.go.jp/kankocho/yosan_zeisei/yosan/index.html',
    'national-tourism-budget-2026': 'https://www.mlit.go.jp/kankocho/content/001982239.pdf',
    'national-tourism-tax-change': 'https://www.mlit.go.jp/kankocho/content/002002297.pdf',
    'national-mof-meti-points': 'https://www.mof.go.jp/policy/budget/budger_workflow/budget/fy2026/seifuan2026/07.pdf',
    'national-mof-meti-overview': 'https://www.mof.go.jp/policy/budget/budger_workflow/budget/fy2026/seifuan2026/08.pdf',
}
METI_CANDIDATES = [
    'https://www.meti.go.jp/main/yosan/yosan_fy2026/pdf/01.pdf',
    'https://www.meti.go.jp/main/yosan/yosan_fy2026/',
    'https://www.meti.go.jp/policy/mono_info_service/ai_semiconductor_frame/ai_semiconductor_frame.html',
]
EDUCATION_ID = 'mof-general-item-f700cbab6d7cb4517a02'
TOURISM_ID = 'mof-general-item-adbc436a75e67aceb6fe'
AI_ID = 'special-エネルギー対策-先端半導体・人工知能関連技術勘定'


def normalized(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))


def download():
    history = json.loads(FETCH_STATE.read_text()) if FETCH_STATE.exists() else []
    csv_urls = [f'https://www.bb.mof.go.jp/server/{y}/csv/DL{y}{n}001.zip'
                for y in [2025, 2026] for n in ['11', '12']]
    for url in [*URLS.values(), *csv_urls, *METI_CANDIDATES]:
        attempt = dict(url=url, retrieved_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
        try:
            fetch(url)
            attempt = dict(status='downloaded', **receipt_for(url))
        except Exception as error:
            attempt.update(status='blocked_or_failed', error_type=type(error).__name__, **failure_details(error))
        history.append(attempt)
        FETCH_STATE.write_text(json.dumps(history, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({k: attempt[k] for k in ['url', 'status', 'failure_category', 'http_status'] if k in attempt}), flush=True)


def read_csv(year, special, revenue=False):
    number = '12' if special else '11'
    url = f'https://www.bb.mof.go.jp/server/{year}/csv/DL{year}{number}001.zip'
    # Validate against prior committed receipts, rather than re-date the source.
    old = json.loads((ROOT / 'data/mof-structured-verification.json').read_text())['originals']
    receipt = next(r for r in old if r['url'] == url)
    content = original_path(url).read_bytes()
    assert hashlib.sha256(content).hexdigest() == receipt['sha256_original'], url
    assert len(content) == receipt['bytes'], url
    member = f'DL{year}{number}001{"a" if revenue else "b"}.csv'
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert archive.getinfo(member).file_size <= MAX_BYTES
        raw = archive.read(member)
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    field = f'令和{year - 2018}年度' + ('予定額(千円)' if special else '要求額(千円)')
    assert field in reader.fieldnames
    records = list(reader)
    assert len({tuple(r.values()) for r in records}) == len(records)
    return records, field, member


def leaf_table(year, category):
    special = category == 'ai'
    records, field, member = read_csv(year, special)
    item = {'education': '教育政策推進費', 'tourism': '国際観光旅客税財源観光振興費'}.get(category)
    ministry, organization = ('文部科学省', '文部科学本省') if category == 'education' else ('国土交通省', '観光庁')
    out = Counter()
    for record in records:
        if special:
            selected = record['特別会計'] == 'エネルギー対策' and record['勘定'] == '先端半導体・人工知能関連技術勘定'
        else:
            selected = (record['所管'], record['組織'], record['項名']) == (ministry, organization, item)
        if selected:
            assert record[field].isdigit()
            # Full item/leaf names prevent merging identical leaf names under
            # distinct items. Codes are not joined across fiscal years.
            out[(record['項名'], record['目名'])] += int(record[field])
    assert out
    return out, member, field


def numerical_review():
    snapshot = {r['id']: r for r in json.loads((ROOT / 'public/data.json').read_text())['rows']}
    reviews = []
    for category, rid in [('education', EDUCATION_ID), ('tourism', TOURISM_ID), ('ai', AI_ID)]:
        tables, locators = {}, {}
        for year in [2025, 2026]:
            tables[year], member, field = leaf_table(year, category)
            total = sum(tables[year].values())
            assert Decimal(str(snapshot[rid][f'amount{year}'])) * 1000 == total
            locators[str(year)] = f'{member} / {field} / 所管・組織・項名・目名（特会は会計・勘定・項名・目名）'
        deltas = []
        for key in sorted(set(tables[2025]) | set(tables[2026])):
            amounts = [tables[y].get(key) for y in [2025, 2026]]
            deltas.append(dict(item_name=key[0], leaf_name=key[1],
                amount_thousand_yen2025=amounts[0], amount_thousand_yen2026=amounts[1],
                amount_status2025='原本に非掲載（完全名称キー）' if amounts[0] is None else '原本照合済み',
                amount_status2026='原本に非掲載（完全名称キー）' if amounts[1] is None else '原本照合済み',
                observed_delta_thousand_yen=None if None in amounts else amounts[1] - amounts[0]))
        reviews.append(dict(row_id=rid, category=category,
            source_ids=[f'mof-csv-{"special" if category == "ai" else "general"}-{y}' for y in [2025, 2026]],
            locators=locators, amount_thousand_yen2025=sum(tables[2025].values()),
            amount_thousand_yen2026=sum(tables[2026].values()),
            delta_thousand_yen=sum(tables[2026].values()) - sum(tables[2025].values()), leaf_details=deltas,
            note='年度ごとの現在年度欄を独立集計。前年度欄・比較欄は使用しない。同名称は制度連続性を保証せず、非掲載nullを表示値ゼロにしない。'))
    education = reviews[0]
    grant = next(r for r in education['leaf_details'] if r['leaf_name'] == '給食費負担軽減交付金')
    assert grant['amount_thousand_yen2025'] is None and grant['amount_thousand_yen2026'] == 164882856
    assert education['delta_thousand_yen'] == 164705365
    education['identified_new_grant_thousand_yen2026'] = 164882856
    education['remaining_item_delta_thousand_yen'] = -177491
    # Revenue is separately reviewed and never added to expenditure.
    revenue = []
    for year in [2025, 2026]:
        rows, field, member = read_csv(year, True, revenue=True)
        values = [dict(item_name=r['項名'], leaf_name=r['目名'], amount_thousand_yen=int(r[field]))
                  for r in rows if r['特別会計'] == 'エネルギー対策' and r['勘定'] == '先端半導体・人工知能関連技術勘定']
        assert sum(v['amount_thousand_yen'] for v in values) == reviews[2][f'amount_thousand_yen{year}']
        revenue.append(dict(year=year, source_id=f'mof-csv-special-{year}', locator=f'{member} / {field}', entries=values))
    reviews[2]['separate_revenue_budget'] = revenue
    assert next(v['amount_thousand_yen'] for v in revenue[1]['entries'] if v['leaf_name'] == '先端半導体・人工知能関連技術公債金') == 787213469
    assert next(v['amount_thousand_yen2026'] for v in reviews[2]['leaf_details'] if v['leaf_name'] == '国債整理基金特別会計へ繰入') == 20242600
    return reviews


def source(sid, title, excerpt, locator, **dates):
    return dict(id=sid, title=title, url=URLS[sid], locator=locator, kind='公式予算・制度資料',
        accessed=CHECKED_AT, published=None,
        retrieved_via='公式原本を既存プロキシ・TLS検証付きHTTPSで取得。必要部分を抽出し、不要な連絡先を除去。',
        sha256_extracted_text=hashlib.sha256(excerpt.encode()).hexdigest(), excerpt=excerpt, **dates)


def check(dimension, status, finding, sources, locator):
    return dict(dimension=dimension, status=status, finding=finding, source_ids=sources, locator=locator)


def build():
    numeric = numerical_review()
    meal_index = text_html(URLS['national-mext-meal-index'])
    meal_index = meal_index[meal_index.index('小学生の保護者の皆さんへ'):meal_index.index('詳しくは通学先')].strip()
    assert '/content/20260407-ope_dev02-000044426_1.pdf' in original_path(URLS['national-mext-meal-index']).read_text()
    meal = pdf_pages(URLS['national-mext-budget-2026'])[53].strip()
    meal = meal[:meal.index('（担当')].strip()
    nmeal = normalized(meal)
    for literal in ['給食費負担軽減交付金', '1,649億円', '国1/2、都道府県1/2', '11か月', '5,200円', '令和7年12月18日', '毎年5月1日現在']:
        assert normalized(literal) in nmeal, literal
    joint_pages = pdf_pages(URLS['national-mext-joint-policy'])
    joint = joint_pages[0].split('１．高校教育')[0] + '\f' + '\f'.join(joint_pages[2:5])
    assert '令和７年 12 月 19 日' in joint and '給食費負担' in joint and '1/2' in joint
    tourism_index = text_html(URLS['national-tourism-budget-index'])
    tourism_index = tourism_index[tourism_index.index('最終更新日：'):tourism_index.index('令和６年度')].strip()
    assert '/kankocho/content/001982239.pdf' in original_path(URLS['national-tourism-budget-index']).read_text()
    assert '令和８年２月18日' in tourism_index
    tourism_pages = pdf_pages(URLS['national-tourism-budget-2026'])
    tourism = '\f'.join(tourism_pages[3:5]).strip()
    for value in ['138,345', '130,000', '57,929', 'デジタル庁一括計上分', '宮内庁計上予算', '10,000', '1,199', '17,490']:
        assert value in tourism, value
    tax = '\f'.join(pdf_pages(URLS['national-tourism-tax-change'])[:1]).strip()
    assert '1,000円から3,000円' in normalized(tax) and '令和8年7月1日' in normalized(tax)
    mof_pages = pdf_pages(URLS['national-mof-meti-points'])
    mof_points = '\f'.join(mof_pages[i] for i in [3, 4, 19]).strip()
    for value in ['6,738.2', '1,617.0', '1,500.0', '3,873.0', '12,188', '国債整理基金', '7,872']:
        assert value in mof_points, value
    mof_overview = '\f'.join(pdf_pages(URLS['national-mof-meti-overview'])[2:4]).strip()
    sources = [
        source('national-mext-meal-index', '学校給食費負担軽減の制度・政策経緯と成立当初予算への公式リンク', meal_index, '制度本文・基準額・政策経緯。2026年4月7日の当初予算リンクを確認。'),
        source('national-mext-budget-2026', '2026年度成立当初予算：給食費負担軽減交付金の創設', meal, 'PDF54頁／印刷53頁：1,649億円（新規）、支援額算式・国県負担割合・政策合意。', document_date='2026-04-07', document_date_precision='day', document_date_role='公式制度入口に記載された成立当初予算公表日'),
        source('national-mext-joint-policy', '三党合意に基づく教育無償化への三省対応（給食部分）', joint.strip(), 'PDF1頁の文書日・作成主体、3〜5頁の給食支援・安定財源・地方負担。高校授業料部分は本項の理由に用いない。', document_date='2025-12-19', document_date_precision='day', document_date_role='本文に記載された三省対応文書日'),
        source('national-tourism-budget-index', '観光庁予算の現行索引と2026年度予算概要の資料日', tourism_index, '現行索引の2026年2月18日予算概要へのリンク。', document_date='2026-08-28', document_date_precision='day', document_date_role='索引の最終更新日（配分決定日ではない）'),
        source('national-tourism-budget-2026', '2026年度観光庁関係予算概要：税財源・政策拡充・集計範囲', tourism, 'PDF4〜5頁／印刷1〜2頁：総括表と他省庁計上・補正・復興枠等の範囲注記。', document_date='2026-02-18', document_date_precision='day', document_date_role='現行公式索引に記載された予算概要公表日（成立前資料）'),
        source('national-tourism-tax-change', '国際観光旅客税の税率拡充と2026年7月以後の適用', tax, 'PDF1頁：背景・要望の結果・1,000→3,000円・適用日・経過措置。'),
        source('national-mof-meti-points', '財務省2026年度経済産業関係予算ポイント：AI半導体の支援と特会総計・純計', mof_points, 'PDF4〜5・20頁／印刷2〜3・18頁：年度当初比較、個別支援、歳入歳出・勘定別総計純計。政府案資料、成立数値は別の財務省CSV。'),
        source('national-mof-meti-overview', '財務省掲載の経済産業関係予算概要：AI半導体支援と勘定の財源', mof_overview, 'PDF3〜4頁：経産省作成資料の財務省掲載版。METI自身のURL取得成功とは扱わない。'),
    ]
    education_sources = ['national-mext-budget-2026', 'national-mext-joint-policy', 'national-mext-meal-index', 'mof-csv-general-2025', 'mof-csv-general-2026']
    tourism_sources = ['national-tourism-budget-2026', 'national-tourism-tax-change', 'national-tourism-budget-index', 'mof-csv-general-2025', 'mof-csv-general-2026']
    ai_sources = ['national-mof-meti-points', 'national-mof-meti-overview', 'mof-csv-special-2025', 'mof-csv-special-2026']
    enrichments = [
        dict(row_id=EDUCATION_ID, explanation_source_ids=education_sources,
            explanation_status='給食支援新設と項別差額を確認・個別地域配分未確認',
            explanation='財務省成立当初CSVで教育政策推進費は44,503.015→209,208.380百万円（+164,705.365）。2026に給食費負担軽減交付金164,882.856百万円を確認し、当該目以外の合計差額は−177.491百万円。文科省成立当初概要は同交付金1,649億円（新規）、2026年4月からの公立小学校段階食材費支援を説明。高校授業料支援と取り違えない。',
            driver_checks=[
                check('制度・対象範囲', '新規制度・国県負担を原本確認', '公立小学校・義務教育学校前期・特別支援小学部の食材費。国1/2・都道府県1/2、県事務費は国費。教育扶助等を優先し、基準超過は保護者徴収可能。国費を全地方負担込みの全国総事業費や執行額と扱わない。', education_sources, '文科省PDF54頁／印刷53頁'),
                check('採択基準', '算定基準のみ確認', '在籍児童数（毎年5月1日、教育扶助等の対象を除く）×基準額×11か月×1/2。完全給食は5,200円（特別支援6,200円）。申請が基準額未満なら申請額。自治体別入力値・申請・実配分の照合は未完了。', education_sources, '文科省PDF54頁／印刷53頁'),
                check('申請額', '個別申請は原本に非掲載', '全国予算概要は個別県の記入済み申請額、児童数入力、充足率を掲載しない。全サイトでの非公表やゼロとは認定しない。', education_sources, '文科省PDF54頁／印刷53頁'),
                check('事業進捗', '実施開始を確認・地域別執行未確認', '2026年4月開始の説明を確認。地方別交付実績、食材購入支出、同期間の執行額は未収載。予算計上を支出済額にしない。', education_sources, '公式制度入口・文科省PDF54頁'),
                check('決定記録', '政策対応文書あり・個別交付記録未確認', '2025年12月19日の文科省・総務省・財務省対応文書、2026成立予算の政策説明を確認。個別都道府県の交付決定・配分根拠・通知現物は未取得。', education_sources, '三省PDF1・3〜5頁／文科省PDF54頁'),
                check('配分時点の関係者', '政策合意の党名あり・個人と地域決定未確認', '文科省成立概要は自民党・公明党・日本維新の会の2025年2月25日及び12月18日合意を明記。これは制度成立の公式経緯であり、個別地域配分への議員の影響や政治的圧力を示さない。個人・党籍・地域決定記録は未確認。', education_sources, '文科省PDF54頁／印刷53頁'),
            ]),
        dict(row_id=TOURISM_ID, explanation_source_ids=tourism_sources,
            explanation_status='税財源拡充と政策内訳を確認・総括表との範囲差あり',
            explanation='財務省成立当初CSVの観光庁項は36,603.391→113,649.771百万円（+77,046.380）。公式税制資料は2026年7月1日以後1,000→3,000円（経過措置あり）とし、過度な混雑対策と地方誘客・需要分散等の財源確保を説明。観光庁総括表の138,345百万円・うち税財源130,000百万円は本項と異なる範囲で、差額を欠落や不一致と認定せず別に扱う。',
            driver_checks=[
                check('制度・対象範囲', '税率改定・政策範囲を確認', '総括表はデジタル庁一括計上の政府情報システム経費や宮内庁の大手休憩所整備を含むと明記。復興枠と2025補正も別表。財務省項別全国額、税財源総括表、地域補助額、補正を合算しない。', tourism_sources, '観光庁予算PDF4〜5頁／印刷1〜2頁'),
                check('採択基準', '政策目的のみ確認', '税財源確保の背景はオーバーツーリズム抑制・地方誘客・需要分散。地方別審査採点・採択基準適用の結果はこの総括表・税制原本では未確認。', tourism_sources, '税制PDF1頁・予算PDF4頁'),
                check('申請額', '個別申請は原本に非掲載', '全国総括表は個別自治体や事業者の申請額・要望充足率を掲載しない。', tourism_sources, '観光庁予算PDF4〜5頁'),
                check('事業進捗', '予算拡充を確認・地域別執行未確認', '総括表で受入環境整備1,199→10,000、地方交通ネットワーク1,833→14,883百万円等を確認。事業者採択・契約・実支出は別時点で未照合。', tourism_sources, '観光庁予算PDF4頁'),
                check('決定記録', '予算概要・税制要望結果あり', '2026年2月18日の予算概要は成立前資料。成立当初の金額は財務省CSVで別確認。税制要望結果は税率と適用日を確認する資料で、個別地域の交付決定記録ではない。', tourism_sources, '公式索引・税制PDF1頁・財務省CSV'),
                check('配分時点の関係者', '未確認', '地域配分決定時点の申請主体、決裁者、議員・首長の党籍、照会・面会と配分への影響は未確認。税率や増額だけから政治的働きかけを認定しない。', tourism_sources, '観光庁全国予算・税制原本'),
            ]),
        dict(row_id=AI_ID, explanation_source_ids=ai_sources,
            explanation_status='財務省原本で支援・財源・総計純計差を確認・METI原本取得不能',
            explanation='成立当初CSVの勘定総計は332,800.000→1,239,004.417百万円（+906,204.417）。財務省予算ポイントはAI・半導体フレームでポスト5G研究開発6,738.2億円（前年1,617.0）、出資1,500.0億円（前年1,000.0）、AI基盤モデル3,873.0億円（新規）等を説明。勘定の2026国債整理基金繰入20,242.600百万円等を独立集計し、総計と純計を区別。経産省サイト候補3URLはHTTP403で未取得のまま保持。',
            driver_checks=[
                check('制度・対象範囲', '支援内訳と総計純計差を確認', '財務省説明では勘定総計12,390億円に対し歳出純計12,188億円。成立CSVで国債整理基金繰入20,242.600百万円を確認。2025/26で費目構成は変わり、同名勘定の増加を同一事業の増加と認定しない。2025補正と2026当初の合算1.5兆円は年度比較に用いない。', ai_sources, '財務省07PDF5・20頁／08PDF3〜4頁・特会CSV b.csv'),
                check('採択基準', '支援政策のみ確認', '研究開発・量産設備等の資金需要/財務基盤・AIモデル開発の支援目的を確認。個別事業の採択基準・評価点・適用結果はこの原本に非掲載。', ai_sources, '財務省07PDF5頁'),
                check('申請額', '個別申請は原本に非掲載', '国の勘定予算・政策資料から企業別申請額、採択額、要望充足率や地域帰属を確定できない。', ai_sources, '財務省07PDF5頁・08PDF3頁'),
                check('財源', '歳入を歳出と分離して原本集計', '2026歳入CSVで半導体AI公債787,213.469百万円、財投投資勘定受入57,832.970、エネルギー需給から受入393,957.958を確認。歳入合計は勘定歳出と一致するが、会計/勘定間繰入は政府全体の追加資金や実支出に読み替えない。', ai_sources, '特会CSV a.csv・財務省07PDF20頁／08PDF4頁'),
                check('事業進捗', '地域別実績・執行未確認', '年度予算は研究開発/出資/運営費等の枠。企業別進捗、設備投資実施、地域別実支出の2025/26同期間比較は未収載。', ai_sources, '財務省07PDF5頁'),
                check('決定記録', '予算政策記録あり・個別決定未確認', '財務省政府案資料と成立CSVを区別。財務省掲載08PDFの一部は経産省作成と明記されるが、METIホストの原本取得済とは扱わない。個別出資・研究開発採択・交付決定記録は未取得。', ai_sources, '財務省07/08PDF・成立特会CSV'),
                check('配分時点の関係者', '未確認', '個別採択決定時点の関係者・議員・首長・党籍と申請/決裁記録は未確認。全国の研究開発予算増額だけから政治的圧力を認定しない。', ai_sources, '財務省全国予算政策資料'),
            ]),
    ]
    originals = [dict(**receipt_for(url), source_ids=[sid], verification=dict(status='retrieved',
        checked_at=CHECKED_AT, method='原本のハッシュ確認と対象頁の政策説明・算定/集計範囲を照合。既存数値照合・比較可能性とは別。',
        locator=next(s['locator'] for s in sources if s['id'] == sid), row_ids=[], fields=[])) for sid, url in URLS.items()]
    previous = json.loads(OUT.read_text()) if OUT.exists() else {}
    failures = [v for v in json.loads(FETCH_STATE.read_text()) if v['status'] == 'blocked_or_failed'] if FETCH_STATE.exists() else previous.get('retrieval_failures', [])
    notes = [dict(ministry=m, status=e['explanation_status'], note=e['explanation'], source_ids=e['explanation_source_ids'])
             for m, e in zip(['文部科学省', '国土交通省', '経済産業省'], enrichments)]
    return dict(schema_version=1, checked_at=CHECKED_AT, sources=sources, rows=[], originals=originals,
        row_enrichments=enrichments, numerical_driver_checks=numeric, retrieval_failures=failures,
        research_notes=notes, coverage_updates=notes,
        verification_summary=dict(driver_enriched_rows=3, numerical_breakdowns=3, new_unique_original_urls=8,
            new_sources=8, preserved_existing_budget_amounts=True, matched_against_prior_original_hashes=True,
            failed_meti_candidates=len({v['url'] for v in failures if v['url'] in METI_CANDIDATES})),
        limitations=['原本転記の一致・政策説明・同制度比較可能性・地域対応・党籍は別の確認状態。既存比較条件を自動昇格しない。',
            '個別申請額・採択基準適用・進捗・決定記録・配分時点の個人と党籍は未確認。金額増減だけから政治的圧力を認定しない。',
            'METI候補のHTTP403は取得不能であり、非掲載・非公表や予算ゼロを意味しない。財務省資料による検証をMETI URL取得成功にしない。'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    if args.fetch:
        download()
    result = build()
    if args.write:
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    else:
        assert json.loads(OUT.read_text()) == result, 'Reviewed national driver report differs from re-extraction'
    print(json.dumps(result['verification_summary'], ensure_ascii=False))


if __name__ == '__main__':
    main()
