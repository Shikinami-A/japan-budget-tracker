"""Review official regional follow-up evidence. Cache-only unless --fetch.

Preserves inherited proxy and verified TLS through the existing official-host
fetcher. Third-round plans, contract amounts and expenditure are kept separate.
"""
import argparse
import hashlib
import json
import re
from html.parser import HTMLParser

from verify_grants_originals import column, integer, table_rows
from verify_mlit_originals import ROOT, fetch, original_path, pdf_pages, receipt_for

OUT = ROOT / 'data/reviewed-regional-followup.json'
CHECKED_AT = '2026-10-09'
URLS = {
    'regional-mlit-inquiry': 'https://www.mlit.go.jp/road/content/002025983.pdf',
    'regional-defense-index': 'https://www.mod.go.jp/j/approach/chouwa/hojokin/',
    'regional-defense-rules-index': 'https://www.mod.go.jp/rdb/s-kanto/effort/plan/grant/index.html',
    'regional-defense-rules': 'https://www.mod.go.jp/rdb/s-kanto/effort/plan/grant/images/ax20070825_00092_000.pdf',
    'regional-defense-round3-2026': 'https://www.mod.go.jp/j/approach/chouwa/hojokin/r8-3/05.pdf',
    'regional-mlit-contracts': 'https://www.mlit.go.jp/sogoseisaku/infra/content/002025334.pdf',
    'regional-mlit-contracts-index': 'https://www.mlit.go.jp/sogoseisaku/infra/sosei_sogo24_fr2_000001_00013.html',
}


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.lines = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.lines.append(data.strip())


def text_html(url):
    receipt_for(url)
    parser = TextParser()
    parser.feed(original_path(url).read_text())
    return '\n'.join(parser.lines)


def defense_round3(prefs):
    url = URLS['regional-defense-round3-2026']
    pages = pdf_pages(url)
    assert '令和８年度' in pages[0] and '単位：百万円' in pages[0]
    assert '１４,７２１百万円' in pages[3] and '１２２市町村' in pages[3]
    values, locations = {}, {}
    prefecture = None
    for page, width, y, words in table_rows(original_path(url)):
        printed_prefecture = column(words, 0, 150)
        if printed_prefecture in prefs:
            prefecture = printed_prefecture
        city = column(words, 340, 465)
        raw_amount = column(words, 480, 560)
        if not city.endswith(('市', '町', '村')) or not re.fullmatch(r'\d[\d,]*', raw_amount):
            continue
        assert prefecture is not None
        key = (prefecture, city.replace('鎌ケ谷', '鎌ヶ谷'))
        assert key not in values
        values[key] = integer(raw_amount)
        locations[key] = {'page': page, 'y_min_points': round(y, 3), 'printed_region': city}
    assert len(values) == 122
    # Independent layout-text extraction checks all numeric municipality cells.
    layout_values = {}
    prefecture = None
    for page in pages[:4]:
        for line in page.splitlines():
            parts = line.split()
            if parts and parts[0] in prefs:
                prefecture = parts[0]
            match = re.search(r'([^\s]+[市町村])\s+(\d[\d,]*)\s*$', line)
            if match:
                key = (prefecture, match[1].replace('鎌ケ谷', '鎌ヶ谷'))
                assert key not in layout_values
                layout_values[key] = integer(match[2])
    assert values == layout_values
    # Published total is independently rounded, so it need not equal row sum.
    assert abs(sum(values.values()) - 14721) <= 61
    return values, locations, '\f'.join(pages[:4]).strip()


def source(sid, title, excerpt, locator, **extra):
    return dict(id=sid, title=title, url=URLS[sid], locator=locator,
                kind='公式配分・調査資料', accessed=CHECKED_AT,
                published=None, retrieved_via='公式原本を既存プロキシ・TLS検証付きHTTPSで取得。本文の必要部分だけ抽出。',
                sha256_extracted_text=hashlib.sha256(excerpt.encode()).hexdigest(),
                excerpt=excerpt, **extra)


def check(dimension, status, finding, locator='PDF1〜2頁'):
    return dict(dimension=dimension, status=status, finding=finding,
                source_ids=['regional-mlit-inquiry'], locator=locator)


def build():
    snapshot = json.loads((ROOT / 'public/data.json').read_text())
    prefs = set(snapshot['prefectures'])
    inquiry = '\f'.join(pdf_pages(URLS['regional-mlit-inquiry'])[:2]).strip()
    for literal in ['事業の進捗状況', '令和８年２月中旬頃', '財務省の承認を受けた', '新規掲載箇所（３事業）', '裁量に委ねられている']:
        assert literal in inquiry, literal
    defense_index = text_html(URLS['regional-defense-index'])
    assert '令和８年度補助金関係について（第３回）' in defense_index
    assert '2026年9月4日更新' in defense_index
    index_html = original_path(URLS['regional-defense-index']).read_text()
    assert 'r8-3/05.pdf' in index_html and 'r8-1/10.pdf' in index_html
    assert 'r7-' not in index_html, 'Recheck prior-year links if the current index changes'
    defense_values, locations, defense_excerpt = defense_round3(prefs)
    contracts = '\f'.join(pdf_pages(URLS['regional-mlit-contracts'])[:2]).strip()
    contracts_index = text_html(URLS['regional-mlit-contracts-index'])
    for literal in ['契約額計', '前年度からの繰越含む', '７月末時点', '65.1', '64.7', '独法等除く']:
        assert literal in contracts, literal
    assert '令和８年７月現在' in contracts_index
    rules_pages = pdf_pages(URLS['regional-defense-rules'])
    rules_excerpt = '\f'.join(rules_pages[i] for i in [0, 2, 7, 12, 13, 14, 15, 16, 19, 20]).strip()
    rules_normalized = re.sub(r'\s+', '', rules_excerpt)
    for literal in ['令和５年３月３１日', '事業の内容及び経費配分書', '収支精算', '交付決定通知書を受理したとき', 'インターネットの利用その他の方法により公表']:
        assert literal in rules_normalized, literal
    rules_index = text_html(URLS['regional-defense-rules-index'])
    # Publish only the relevant official-body explanation, not the contact footer.
    rules_index = rules_index[rules_index.index('特定防衛施設周辺整備調整交付金の交付'):rules_index.index('本件に関するお問い合わせ先')].strip()
    assert 'images/ax20070825_00092_000.pdf' in original_path(URLS['regional-defense-rules-index']).read_text()
    sources = [
        source('regional-mlit-inquiry', '道路配分の決定手続・要望・照会に関する国交省精査結果', inquiry, 'PDF1〜2頁。公式説明と、申請額・決定文書現物の非掲載を区別。'),
        source('regional-defense-index', '防衛省実施計画（補助金関係）の現行公式索引', defense_index, '第1〜3回掲載区分・第3回特定防衛施設周辺整備調整交付金へのリンク・末尾更新日。', document_date='2026-09-04', document_date_precision='day', document_date_role='ページ更新日（各PDFの決定日ではない）'),
        source('regional-defense-round3-2026', '2026年度第3回特定防衛施設周辺整備調整交付金実施計画', defense_excerpt, 'PDF1〜4頁。市町村名・金額欄、4頁全国合計14,721百万円。第3回の根拠は公式索引。'),
        source('regional-mlit-contracts', '公共事業の執行状況（契約額・繰越・不用の公式説明）', contracts, 'PDF1〜2頁。2頁は7月末契約額。当初予算欄は前年度からの補正繰越額を含み独法等を除く。'),
        source('regional-mlit-contracts-index', '公共事業の執行状況の公式掲載頁', contracts_index, '最新の公共事業の執行状況へのリンク。掲載頁の「令和8年7月現在」とPDF本文「7月末時点」で期間を確認。'),
    ]
    sources.extend([
        source('regional-defense-rules-index', '特定防衛施設周辺整備調整交付金の公式制度説明・要綱リンク', rules_index, '南関東防衛局の制度説明と交付要綱原本リンク。連絡先等のフッターは除去。'),
        source('regional-defense-rules', '特定防衛施設周辺整備調整交付金交付要綱（公式掲載版）', rules_excerpt, 'PDF1・3・8・13〜17・20〜21頁。申請書類、遂行状況・実績報告、基金の公表義務。2025/2026適用版の一致は未確認。', document_date='2023-03-31', document_date_precision='day', document_date_role='掲載PDFに記載された最終改正日。2025/2026適用版の確定日ではない'),
    ])
    rows = []
    for (pref, city), amount in defense_values.items():
        rid = f'regional-defense-round3-{pref}-{city}'
        rows.append(dict(id=rid, ministry='防衛省', program='特定防衛施設周辺整備調整交付金（第3回）',
                         region=city, prefecture=pref, basis='実施計画（第3回）', account='会計区分未確認', unit='百万円',
                         amount2025=None, amount2026=amount, amount2025_status='未収載・同回原本未確認', amount2026_status='原本照合済み',
                         source_ids=['regional-defense-round3-2026', 'regional-defense-index'],
                         period2025='2025年度第3回原本未確認', period2026='2026年度第3回実施計画',
                         evidence_status='原本一部照合済み', comparability='参考・配分回次未確定', scope='第3回掲載市町村別金額',
                         precision='単位未満四捨五入', source_locator2026=locations[(pref, city)],
                         note='2025年度同回は現行公式索引にリンクがなく未収載。非掲載・ゼロとは認定しない。第1回との追加・差替え・累計の関係と会計区分は原本説明が未確認のため、既存第1回額を上書き・合算せず別表に保存。支出済額ではない。'))
    base_checks = [
        check('申請額', '原本に非掲載', '要望提出は例年1〜2月、都道府県が管内市町村分も含めて地方整備局等へ提出すると説明。2市町の2025/2026年度申請額・要望充足率はこの原本に非掲載。'),
        check('採択基準', '一般説明のみ確認', '限られた予算の中で、地方公共団体の要望・事業進捗・重点配分対象の有無等を総合勘案すると説明。個別評価・採点・配分算式とその適用結果はこの原本に非掲載。'),
        check('事業進捗', '原本に非掲載', '配分要因として進捗状況への言及はあるが、個別事業の進捗率・完了・工期・必要事業量はこの原本に非掲載。'),
        check('決定記録', '手続の公式説明のみ確認', '2026年4月7日予算成立後に国交省が財務省へ実施計画承認申請し承認を受け、同日に内定通知を発出したと説明。承認申請書・承認文書・内定通知現物および配分理由別内訳はこの原本に非掲載。'),
        check('配分時点の関係者', '出来事の記載あり・党籍未確認', '2025年10月20日那珂川町長、2026年1月20日那須烏山市長から県事業を含む要望。2026年2月中旬頃簗議員から照会。決定後の同議員への説明日時は不明と記載。当該時点の全関係者・所属党・支持関係と照会の配分への影響は未確認。', 'PDF2頁'),
    ]
    enrichments = []
    for municipality in ['那須烏山市', '那珂川町']:
        for i, scope in enumerate(['市町事業', '県事業', '合計']):
            checks = [dict(c) for c in base_checks]
            finding = '国は社会資本総合整備計画単位で配分を決定し、要素事業単位は自治体裁量。市町事業と地域内県事業の合計・内訳は別の対象範囲。'
            if municipality == '那須烏山市':
                finding += '那須烏山市の64%減との説明は過年度整理対象に限定。2026新規3事業を含めた市事業は26.2%減と説明。新規3事業の名称と金額内訳はこの原本に非掲載。'
            checks.insert(3, check('制度・対象範囲', '対象範囲の差を確認', finding, 'PDF1頁'))
            enrichments.append(dict(row_id=f'road-{municipality}-{i}', explanation_source_ids=['regional-mlit-inquiry'],
                                    explanation_status='公式手続・対象範囲説明あり・個別配分理由未確認', driver_checks=checks))
    defense_rule_checks = [
        ('申請額', '原本に非掲載', '要綱第4条は事業内容・経費配分、全体計画、収支予算の添付を要求。掲載要綱・空欄様式から山都町の記入済み2025/2026申請額は確認できない。', 'PDF8頁、第4条'),
        ('採択基準', '制度手続のみ確認', '掲載要綱では関連市町村・対象経費・交付事務主体を確認。山都町の年度別配分算定入力、採択基準の適用結果・増額理由は未確認。', 'PDF3頁、第2〜3条'),
        ('事業進捗', '原本に非掲載', '第7〜9条は遂行困難時報告、12月末遂行状況、完了時収支精算・完了検査等調書、年度内未完了時出来高工程表を定める。山都町の記入済み進捗報告・実績報告は未取得。', 'PDF13〜17頁、第7〜9条'),
        ('制度・対象範囲', '適用版未確認', '公式局掲載PDFの最終改正は2023年3月31日。2025/2026に適用された版との一致や対象施設運用の変化は未確認。', 'PDF1頁、改正履歴'),
        ('決定記録', '原本に非掲載', '基金では通知受理後に目的・内容・始期終期・経費・交付額等を公表し、終了後に評価書を公表する規定。山都町の交付決定通知・配分算定記録現物は未取得。基金造成への交付と基金処分・実支出は別時点。', 'PDF20〜21頁、第11条'),
        ('配分時点の関係者', '未確認', '要綱第2条は地方防衛局長等が交付事務を行う旨。個別決定時点の関係者、議員・首長の党籍、申請・照会・面会の記録は未確認。', 'PDF3頁、第2条'),
    ]
    enrichments.append(dict(row_id='defense-熊本県-山都町', explanation_source_ids=['regional-defense-rules', 'regional-defense-rules-index'], explanation_status='制度手続の公式根拠あり・個別増額理由未確認', driver_checks=[dict(dimension=d, status=s, finding=f, locator=l, source_ids=['regional-defense-rules', 'regional-defense-rules-index']) for d, s, f, l in defense_rule_checks]))
    notes = [
        dict(ministry='国土交通省', status='地域別実支出は未収載',
             note='公共事業の執行状況原本は2025/2026の7月末契約額と契約率の全国表で、支出済額・地域別実支出を掲載しない。当初予算欄に前年度補正の繰越を含み、独法等を除く。財務省一般会計累計支出済額と混ぜない。掲載頁の「7月現在」とPDF本文の「7月末時点」を原本で確認。検索スニペットの旧「6月現在」表記を使用しない。',
             source_ids=['regional-mlit-contracts', 'regional-mlit-contracts-index']),
        dict(ministry='防衛省', status='第3回原本追加・年度比較未確定',
             note='9月4日更新の現行索引から2026第3回122市町村の原本を取得・数値照合。公表合計14,721百万円。2025同回は現行索引にリンクがなく未収載。第1回12,119百万円との追加/差替え/累計関係が未確認のため合算しない。いずれも実施計画で実支出ではない。',
             source_ids=['regional-defense-round3-2026', 'regional-defense-index']),
        dict(ministry='防衛省', status='要綱の手続確認・個別増額理由未確認', note='許可済み公式局ホストに掲載された交付要綱で、申請書類・進捗/実績報告・基金目的や交付額の公表手続を確認。掲載版の最終改正2023年3月31日と2025/2026適用版の一致は未確認。山都町の個別申請額・算定記録・決定通知・実支出は未取得。', source_ids=['regional-defense-rules', 'regional-defense-rules-index']),
    ]
    originals = []
    for sid, url in URLS.items():
        matched = sid == 'regional-defense-round3-2026'
        originals.append(dict(**receipt_for(url), source_ids=[sid], verification=dict(
            status='matched' if matched else 'retrieved', checked_at=CHECKED_AT,
            method='PDF座標列とlayout本文の独立抽出を122市町村全件で一致検査。公表総額と丸め許容差を検査。' if matched else '公式原本ハッシュを検査し、本文・索引の表記と手続を確認。金額比較の検証状態には加算しない。',
            locator='PDF1〜4頁、市町村・金額欄' if matched else '公式本文と掲載リンク',
            row_ids=[r['id'] for r in rows] if matched else [], fields=['amount2026'] if matched else [],
            values={r['id']: {'amount2026': r['amount2026']} for r in rows} if matched else {})))
    return dict(schema_version=1, checked_at=CHECKED_AT, sources=sources, rows=rows, originals=originals,
                row_enrichments=enrichments, research_notes=notes, coverage_updates=notes,
                verification_summary=dict(new_reference_rows=len(rows), independently_matched_amounts=len(rows),
                                          defense_round3_published_total=14721, defense_round3_rounded_row_sum=sum(defense_values.values()),
                                          missing_previous_year_status='未収載（現行索引に同回リンクなし、非掲載とは未認定）',
                                          driver_enriched_rows=len(enrichments), new_unique_original_urls=6,
                                          contract_amount_is_not_expenditure=True),
                limitations=['第3回と第1回の金額を合算しない。2025同回の不存在やゼロを意味しない。',
                             '道路の個別申請額・進捗・採点・決定文書現物・配分時点の党籍は未確認。金額増減や照会の存在から政治的圧力を認定しない。',
                             '契約額は支出済額ではなく、当初配分と補正繰越を混ぜた地域別実支出比較も作らない。'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    if args.fetch:
        for url in URLS.values():
            fetch(url)
    result = build()
    if args.write:
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    else:
        assert json.loads(OUT.read_text()) == result, 'Reviewed follow-up differs from official re-extraction'
    print(json.dumps(result['verification_summary'], ensure_ascii=False))


if __name__ == '__main__':
    main()
