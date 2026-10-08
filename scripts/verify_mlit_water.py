"""Verify non-road MLIT original regional allocation tables; no network by default.

Official PDFs distinguish total business cost from national budget expenditure.
Image-only water-management tables are inventoried, not promoted to verified rows.
"""
import argparse
import hashlib
import json
import re
import unicodedata
from urllib.error import HTTPError, URLError

from fetch_sources import failure_details
from verify_grants_originals import table_rows, column, integer
from verify_mlit_originals import ROOT, PREFS, BUREAUS, fetch, original_path, receipt_for as cache_receipt_for, pdf_pages
from verify_regional_followup import text_html

OUT = ROOT / 'data/reviewed-mlit-water.json'
CHECKED_AT = '2026-10-09'
DOCUMENTS = {
    'port': {2025: '001881409', 2026: '001994797'},
    'airport': {2025: '001881411', 2026: '001994798'},
    'water': {2025: '001881401', 2026: '001994793'},
}
ENTRIES = {2025: 'https://www.mlit.go.jp/report/press/kanbo05_hh_000289.html',
           2026: 'https://www.mlit.go.jp/report/press/kanbo05_hh_000304.html'}
PUBLISHED = {2025: '2025-04-01', 2026: '2026-04-07'}
PREF_ALIASES = {re.sub(r'[都府県]$', '', p) if p != '北海道' else p: p for p in PREFS}
# 港湾の小計「北海道」「沖縄」 are also prefecture-level leaf rows.
PREF_ALIASES.update({p: p for p in PREFS})
REGION_LABELS = ['北海道', '東北', '関東', '北陸', '中部', '近畿', '中国', '四国', '九州', '沖縄']
REGION_TO_BUREAU = dict(zip(REGION_LABELS, BUREAUS))


def historical_receipt(url, report_path):
    """Keep the first reviewed receipt when a fresh download is identical.

    The cache manifest retains the new download timestamp separately. An
    updated original or destination requires review, never a silent rewrite
    of previously committed evidence.
    """
    current = cache_receipt_for(url)
    if report_path.exists():
        saved = [r for r in json.loads(report_path.read_text()).get('originals', []) if r['url'] == url]
        if saved:
            first = saved[0]
            for key in ['url', 'final_url', 'sha256_original', 'bytes', 'content_type']:
                assert current[key] == first[key], f'Official original changed ({key}); manual review required: {url}'
            current['retrieved_at_utc'] = first['retrieved_at_utc']
    return current


def receipt_for(url):
    return historical_receipt(url, OUT)


def url_for(family, year):
    return f'https://www.mlit.go.jp/report/press/content/{DOCUMENTS[family][year]}.pdf'


def canon(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC',value))


def numeric(value):
    # Blank and dash are absent table cells, never zero.
    return integer(value) if re.fullmatch(r'\d[\d,]*', value) else None


def port_values(url, year):
    pages = pdf_pages(url)
    assert f'令和{year-2018}年度' in canon(pages[0])
    assert all('事業費ベース' in pages[i] and '単位：百万円' in pages[i] for i in [3, 4])
    assert '下関市を除いた配分額' in pages[4] and '下関市に計上した配分額' in pages[4]
    assert '四捨五入' in pages[3] and '四捨五入' in pages[4]
    result = {'direct': {}, 'subsidy': {}}
    positions = {'direct': {}, 'subsidy': {}}
    subtotals, excluded = {}, {}
    raw_subsidies = {}
    for pg, width, y, words in table_rows(original_path(url)):
        if pg not in [4, 5]:
            continue
        label = re.sub(r'※[12]', '', canon(column(words, 0, 115)))
        if pg == 4:
            amount = numeric(column(words, 480, 550))
            if amount is None:
                continue
            if label == '合計':
                subtotals['direct'] = amount
            elif label in REGION_LABELS:
                key = REGION_TO_BUREAU[label]
                assert key not in result['direct']
                result['direct'][key] = amount
                positions['direct'][key] = [dict(page=pg, printed_label=label, y_min_points=round(y,3), column='合計・計')]
        else:
            amount = numeric(column(words, 425, 475))
            if amount is None:
                continue
            if label == '小計':
                subtotals['subsidy'] = amount
            elif label == '合計':
                subtotals['subsidy_with_nonregional'] = amount
            elif label in ['民間等', '独立行政法人等']:
                excluded[label] = amount
            elif label in PREF_ALIASES or label == '山口下関':
                assert label not in raw_subsidies
                raw_subsidies[label] = amount
                key = '山口県' if label == '山口下関' else PREF_ALIASES[label]
                result['subsidy'][key] = result['subsidy'].get(key, 0) + amount
                positions['subsidy'].setdefault(key, []).append(dict(page=pg, printed_label=label, printed_amount=amount,
                                                                       y_min_points=round(y,3), column='合計・計'))
    assert len(result['direct']) == 10
    assert len(result['subsidy']) == (39 if year == 2025 else 38)
    assert len(positions['subsidy']['山口県']) == 2
    # A separate layout-text extraction checks the rightmost total cell.
    for family, page_indexes in [('direct',[3]),('subsidy',[4])]:
        second = {}
        for page_index in page_indexes:
            for line in pages[page_index].splitlines():
                match = re.match(r'^\s*(北海道|東\s*北|関\s*東|北\s*陸|中\s*部|近\s*畿|中\s*国|四\s*国|九\s*州|沖\s*縄|山口下関|[^\d\s]+(?:\s+[^\d\s]+)*)\s+(?:※[12]\s+)?([\d,]+(?:\s+[\d,]+)*)\s*$', line)
                if not match:
                    continue
                label, numbers = canon(match[1]), match[2]
                amount = integer(numbers.split()[-1])
                if family == 'direct' and label in REGION_LABELS:
                    assert label not in second
                    second[label] = amount
                    assert result[family][REGION_TO_BUREAU[label]] == amount
                if family == 'subsidy' and label in raw_subsidies:
                    assert label not in second
                    second[label] = amount
                    assert raw_subsidies[label] == amount
        expected = {label: result['direct'][REGION_TO_BUREAU[label]] for label in REGION_LABELS} if family=='direct' else raw_subsidies
        assert second == expected
    for family in ['direct','subsidy']:
        assert abs(sum(result[family].values())-subtotals[family]) <= len(result[family]) / 2 + 1
    assert len(excluded) == 1
    assert abs(subtotals['subsidy'] + sum(excluded.values()) - subtotals['subsidy_with_nonregional']) <= 1
    assert subtotals['direct'] == (208584 if year == 2025 else 212137)
    assert subtotals['subsidy'] == (40787 if year == 2025 else 37783)
    return result, positions, subtotals, '\f'.join(pages[3:5]).strip(), excluded


def airport_values(url, year):
    pages = pdf_pages(url)
    assert f'令和{year-2018}年度' in canon(pages[0])
    assert '総事業費' in pages[2] and '地域配分を行わないため' in pages[2]
    assert '全て本省配分' in pages[2]
    assert all('単位：百万円' in pages[i] for i in [9,10])
    result = {'direct':{}, 'subsidy':{}}
    positions = {'direct':{}, 'subsidy':{}}
    subtotals = {}
    last_bureau = None
    mode = 'direct'
    for pg,width,y,words in table_rows(original_path(url)):
        if pg not in [10,11]:
            continue
        label = canon(column(words,0,145))
        text = ''.join(w.text or '' for w in words)
        if '補助事業' in text:
            mode = 'subsidy'
        if label in ['東京航空局', '大阪航空局']:
            last_bureau = label
        amount = numeric(column(words,145,215))
        if amount is None:
            continue
        if label == '合計':
            subtotals[mode] = amount
        elif mode == 'direct' and last_bureau:
            assert last_bureau not in result['direct']
            result['direct'][last_bureau] = amount
            positions['direct'][last_bureau] = [dict(page=pg,y_min_points=round(y,3),column='空港整備事業',printed_label=last_bureau)]
            last_bureau = None
        elif mode == 'subsidy' and label in PREF_ALIASES:
            key = PREF_ALIASES[label]
            assert key not in result['subsidy']
            result['subsidy'][key] = amount
            positions['subsidy'][key] = [dict(page=pg,y_min_points=round(y,3),column='空港整備事業',printed_label=label)]
    assert len(result['direct']) == 2
    assert len(result['subsidy']) == 32
    # Numeric zero for 大分 is actually printed; this is a rounded display value.
    assert result['subsidy']['大分県'] == 0
    assert subtotals == {'direct':141825 if year==2025 else 142979,'subsidy':18149 if year==2025 else 17644}
    for family in ['direct','subsidy']:
        assert abs(sum(result[family].values())-subtotals[family]) <= len(result[family])/2+1
    # Check county numbers in a second layout extraction, excluding regional subtotals.
    second = {}
    for page in pages[9:11]:
        for line in page.splitlines():
            m = re.match(r'^\s*([^\d]+?)\s+(\d[\d,]*)\s+(\d+\.\d+|皆増)',line)
            if m and canon(m[1]) in PREF_ALIASES:
                key = PREF_ALIASES[canon(m[1])]
                assert key not in second
                second[key] = integer(m[2])
    assert second == result['subsidy']
    return result, positions, subtotals, '\f'.join([pages[2],*pages[9:11]]).strip(), {}


def make_source(sid,url,title,excerpt,locator,year,published_verified):
    return dict(id=sid,title=title,url=url,kind='予算・地域配分資料',accessed=CHECKED_AT,
                published=PUBLISHED[year] if published_verified else None,
                published_date_evidence='同年度国交省当初配分の公式発表頁（リンクと発表日を原本確認）' if published_verified else None,
                document_date=f'{year}-04',document_date_precision='month',locator=locator,
                retrieved_via='公式原本を既存プロキシ・TLS検証付きHTTPSで取得、pdftotextの座標列とlayoutで照合',
                sha256_extracted_text=hashlib.sha256(excerpt.encode()).hexdigest(),excerpt=excerpt)


def build():
    sources,originals,rows,notes,checks,failures = [],[],[],[],{},[]
    extracted = {}
    entry_ok = {}
    for year,url in ENTRIES.items():
        if not original_path(url).exists():
            entry_ok[year] = False
            continue
        html = original_path(url).read_text()
        assert ('令和7年4月1日' if year==2025 else '令和8年4月7日') in html
        entry_ok[year] = True
        text = text_html(url).split('お問い合わせ先')[0].strip()
        sid = f'mlit-water-entry-{year}'
        sources.append(make_source(sid,url,f'{year}年度国交省当初予算配分の公式発表',text,'公式発表日・組織別原本リンク。お問い合わせ先以降を除去。',year,True))
        originals.append(dict(**receipt_for(url),source_ids=[sid],verification=dict(status='retrieved',checked_at=CHECKED_AT,
            method='公式発表日・年度・対象PDFへのリンクを確認。',locator='HTML',row_ids=[],fields=[])))
    for family in ['port','airport','water']:
        for year in [2025,2026]:
            url = url_for(family,year)
            sid = f'mlit-water-{family}-{year}'
            if not original_path(url).exists():
                failures.append(dict(url=url,status='未取得',family=family,year=year))
                continue
            if entry_ok[year]:
                assert DOCUMENTS[family][year] + '.pdf' in original_path(ENTRIES[year]).read_text()
            if family == 'water':
                pages = pdf_pages(url)
                regional_indexes = [7,8,9] if year==2025 else [6,7,8]
                assert all(not re.search(r'\d[\d,]{3}',pages[i]) for i in regional_indexes)
                excerpt = '\f'.join(pages[:3]).strip() + '\n\n地域表は画像として掲載。数値抽出・照合未完了（OCR候補は不採用）。'
                source = make_source(sid,url,f'{year}年度水管理・国土保全局予算配分概要（地域表数値未照合）',excerpt,
                                     '目次と予算配分総括表。地域表は2025 PDF8〜10頁／2026 PDF7〜9頁の画像。国庫債務負担行為は別表。',year,entry_ok[year])
                source['retrieved_via'] = '公式PDF原本取得、地域表の画像掲載を確認。地域数値は未照合。'
                sources.append(source)
                originals.append(dict(**receipt_for(url),source_ids=[sid],verification=dict(status='retrieved',checked_at=CHECKED_AT,
                     method='原本ハッシュ・年度・地域表の画像掲載を確認。OCR候補と小計の不一致が解消しないため数値照合済みにはしない。',locator=source['locator'],row_ids=[],fields=[])))
                continue
            extractor = port_values if family=='port' else airport_values
            values,positions,totals,excerpt,excluded = extractor(url,year)
            extracted[(family,year)] = (values,positions)
            checks[f'{family}-{year}'] = dict(published_totals=totals,rounded_row_sums={k:sum(v.values()) for k,v in values.items()},
                                           row_counts={k:len(v) for k,v in values.items()},excluded_nonregional=excluded)
            title = f'{year}年度' + ('港湾局' if family=='port' else '航空局') + '当初配分（地域別事業費・国費ではない）'
            sources.append(make_source(sid,url,title,excerpt,
                                      'PDF4〜5頁、直轄/補助、合計の計列。小計・ブロック計を県行と重複加算しない。' if family=='port' else
                                      'PDF3・10〜11頁、総事業費定義・直轄2航空局・補助県の空港整備事業欄。',year,entry_ok[year]))
    for family in ['port','airport']:
        program_prefix = '港湾・港湾海岸' if family=='port' else '空港整備'
        for mode in ['direct','subsidy']:
            values_by_year = {year:extracted.get((family,year),({},{}))[0].get(mode,{}) for year in [2025,2026]}
            regions = sorted(set(values_by_year[2025])|set(values_by_year[2026]))
            for region in regions:
                a,b = (values_by_year[y].get(region) for y in [2025,2026])
                rid = f'mlit-water-{family}-{mode}-{region}'
                is_pref = mode=='subsidy'
                r = dict(id=rid,ministry='国土交通省',program=program_prefix+('補助事業' if is_pref else '直轄事業')+'（事業費・国費ではない）',
                         region=region,prefecture=region if is_pref else None,basis='当初配分（事業費）',account='会計別未分解',unit='百万円',
                         amount2025=a,amount2026=b,source_ids=[f'mlit-water-{family}-{y}' for y in [2025,2026] if (family,y) in extracted],
                         period2025='2025年度当初配分',period2026='2026年度当初配分',evidence_status='公式原本照合済み',
                         comparability='同範囲' if a is not None and b is not None else '片年度非掲載' if all((family,y) in extracted for y in [2025,2026]) else '片年度未取得',
                         scope='都道府県別事業費（受取自治体のみではない）' if is_pref else '支分部局等の広域管内事業費',
                         precision='百万円への端数処理。大分県航空補助の印刷0は丸め値で厳密ゼロとは限らない。' if family=='airport' else '百万円・四捨五入',
                         note='地方負担等を含む事業費を比較。国費・支出済額ではなく、一般会計所管予算・他配分表と合算しない。ブロック小計と県内訳の二重計上を除外。非掲載はnullを維持。')
                if not is_pref:
                    r['note'] += '広域管内額を特定県・市町村・議員に割り当てない。'
                if family=='port':
                    r['note'] += '港湾整備と港湾海岸の合計・計欄。貸付・調査・工事諸費等の表外額を含めない。民間等/独立行政法人等の非地域枠を県へ配賦しない。'
                    if region=='山口県':r['note'] += '山口欄（下関市を除く）と山口下関欄を加算して全県化。九州/中国ブロック小計は加算しない。'
                else:
                    r['note'] += '空港会社/地方公共団体の管理空港に関係する事業と環境対策を含む。航空路整備・調査・災害復旧・工事諸費等は地域配分対象外として表から除外。対象空港・箇所数の変化は個別事情未照合。印刷倍率から欠損を0と補わない。'
                for year,amount in [(2025,a),(2026,b)]:
                    r[f'amount{year}_status'] = '原本照合済み' if amount is not None else '原本表に非掲載' if (family,year) in extracted else '未取得'
                    if amount is not None:r[f'source_locator{year}'] = extracted[(family,year)][1][mode][region]
                rows.append(r)
        for year in [2025,2026]:
            if (family,year) not in extracted:continue
            url=url_for(family,year)
            rids=[r['id'] for r in rows if r['id'].startswith(f'mlit-water-{family}-')]
            # Nulls are verified as absent rows of this original, not numeric zero.
            values={r['id']:{f'amount{year}':r[f'amount{year}']} for r in rows if r['id'] in rids}
            originals.append(dict(**receipt_for(url),source_ids=[f'mlit-water-{family}-{year}'],verification=dict(
                status='matched',checked_at=CHECKED_AT,method='PDF座標列とlayout本文を照合、全国小計・県/局一意性・単位・重複排除・丸め許容差を検査。',
                locator='PDF4〜5頁' if family=='port' else 'PDF3・10〜11頁',row_ids=rids,fields=[f'amount{year}'],values=values)))
    notes.append(dict(ministry='国土交通省',status='港湾・航空地域配分を追加',note=f'当初事業費の地域表{len(rows)}行を原本照合。港湾は補助山口県の下関除外欄と下関欄を統合し、ブロック小計・全国合計を二重加算しない。航空/港湾の非掲載はnullであり、新規採択・廃止の理由は未確認。広域局管内を県へ配賦しない。',source_ids=[s['id'] for s in sources if '-port-' in s['id'] or '-airport-' in s['id']]))
    notes.append(dict(ministry='国土交通省',status='水管理地域表は原本取得・数値未照合',note='河川・ダム・砂防・海岸、上下水道/水道/下水道の配分原本を両年度取得。地域表は画像のみで、OCR候補の読み違いと小計との不一致が未解消のため比較行に採用しない。取得不能と区別する。国庫債務負担行為は別表で、事業費・国費・利水者負担・表外支出の範囲確認も必要。',source_ids=[f'mlit-water-water-{y}' for y in [2025,2026] if original_path(url_for('water',y)).exists()]))
    return dict(schema_version=1,checked_at=CHECKED_AT,sources=sources,rows=rows,originals=originals,research_notes=notes,
                verification_summary=dict(new_rows=len(rows),fully_nonmissing_rows=sum(r['amount2025'] is not None and r['amount2026'] is not None for r in rows),
                partial_missing_rows=sum(r['amount2025'] is None or r['amount2026'] is None for r in rows),published_tables=checks,
                water_regional_numeric_status='原本取得済み・画像表数値未照合'),failures=failures,
                limitations=['全国小計・地域ブロック・県・非地域枠を重複加算しない。','事業費を国費・実支出へ読み替えない。','画像表のOCR結果を未検証のまま比較値に採用しない。','事業箇所・申請額・進捗・決定理由・政治的影響はこの表の数値照合では確認しない。'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fetch',action='store_true');p.add_argument('--write',action='store_true');args=p.parse_args()
    if args.fetch:
        # Continue other official downloads on a partial HTTP or proxy failure.
        for url in [*ENTRIES.values(),*[url_for(f,y) for f in DOCUMENTS for y in [2025,2026]]]:
            try:fetch(url)
            except (HTTPError,URLError,TimeoutError,ValueError) as error:
                print(json.dumps(dict(url=url,status='blocked_or_failed',**failure_details(error)),ensure_ascii=False))
    result=build()
    if args.write:OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    else:assert json.loads(OUT.read_text())==result,'Reviewed allocation differs from official re-extraction'
    print(json.dumps(result['verification_summary'],ensure_ascii=False))

if __name__=='__main__':main()
