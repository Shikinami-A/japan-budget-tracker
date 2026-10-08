"""Verify remaining MLIT utilities image columns and their financial scope.

No network by default. --ocr repeats numeric cell extraction independently of
both visual transcriptions. --validate-only checks the committed additions
without original caches. Original binaries and image crops remain ignored.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess
from urllib.error import HTTPError, URLError

from fetch_sources import failure_details
from verify_mlit_originals import ROOT, fetch, original_path, pdf_pages
from verify_mlit_water import ENTRIES, PUBLISHED, historical_receipt, url_for
from verify_mlit_water_images import ocr_cell

OUT=ROOT/'data/reviewed-mlit-water-utilities.json'
REVIEW=ROOT/'data/mlit-water-utilities-review.json'
INDEPENDENT=ROOT/'data/mlit-water-utilities-independent.json'
OCR=ROOT/'data/mlit-water-utilities-ocr.json'
PRIOR=ROOT/'data/reviewed-mlit-water-images.json'
CACHE=ROOT/'.cache/water-utilities-review'
CHECKED_AT='2026-10-09'
INDEX={2025:'https://www.mlit.go.jp/page/kanbo05_hy_003321.html',2026:'https://www.mlit.go.jp/page/kanbo05_hy_003397.html'}
TOKYO={2025:'https://www.mlit.go.jp/page/content/001879743.pdf',2026:'https://www.mlit.go.jp/page/content/001994565.pdf'}
TOKYO_PAGES={2025:[26,27,28],2026:[24,25]}
SOURCE_TEXT=ROOT/'data/source-text'


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def read_review():
    review=json.loads(REVIEW.read_text());independent=json.loads(INDEPENDENT.read_text());ledger=json.loads(OCR.read_text())
    assert independent['primary_values_seen_before_transcription'] is False
    cells={(c['year'],c['region'],c['column']):c for c in ledger['cells']}
    expected=set()
    for t in review['tables']:
        second=next(v for v in independent['tables'] if v['fiscal_year']==t['year'])
        assert second['url']==t['url'] and second['sha256_original']==t['sha256_original'] and second['pdf_page']==t['pdf_page']
        assert {r['prefecture']:r['amounts'] for r in second['rows']}==t['values']
        assert [r['prefecture'] for r in second['rows']]==list(t['values'])
        assert [re.sub('[都府県]$','',p) for p in t['values']]==t['printed_region_order']
        assert len(t['values'])==47 and t['columns']==['上下水道','水道']
        for c,total in enumerate(t['published_totals']):
            numeric=[v[c] for v in t['values'].values() if v[c] is not None]
            assert abs(sum(numeric)-total)<=len(numeric)/2+1
        for region,values in t['values'].items():
            for c,value in enumerate(values):
                key=(t['year'],region,c);expected.add(key);cell=cells[key]
                assert cell['reviewed']==value and cell['match']==(cell['ocr']==value)
                assert re.fullmatch('[a-f0-9]{64}',cell['sha256_cell_png'])
                if not cell['match'] or value is None and cell['ocr_raw']!='-':
                    assert cell.get('visual_review_status')=='公式原本セルを再確認' and cell.get('visual_review_value')==value
    assert len(expected)==len(cells)==len(ledger['cells'])==188 and expected==set(cells)
    return review,ledger


def crop_jobs(review):
    from PIL import Image
    CACHE.mkdir(parents=True,exist_ok=True)
    jobs=[]
    for t in review['tables']:
        year=t['year'];page=t['pdf_page'];prefix=CACHE/f'water{year}'
        subprocess.run(['pdftoppm','-f',str(page),'-l',str(page),'-scale-to','2200','-png',str(original_path(t['url'])),str(prefix)],check=True)
        image=Image.open(Path(f'{prefix}-{page:02}.png'));assert image.size==(1556,2200)
        xs=[(924,1029),(1041,1146)] if year==2025 else [(930,1038),(1050,1157)]
        first,step=(265.5,28.70) if year==2025 else (293.5,28.79)
        for i,(region,values) in enumerate(t['values'].items()):
            for c,value in enumerate(values):
                center=first+i*step;box=(xs[c][0],round(center-9),xs[c][1],round(center+9))
                crop=image.crop(box).resize((4*(box[2]-box[0]),4*(box[3]-box[1])));path=CACHE/f'{year}-{i}-{c}.png';crop.save(path)
                jobs.append((year,'utilities',region,c,value,path,box))
    return jobs


def tokyo_checks(year,table):
    pages=pdf_pages(TOKYO[year]);selected=[pages[p-1] for p in TOKYO_PAGES[year]]
    assert all('都道府県名:東京都' in re.sub(r'\s+','',p).replace('：',':') for p in selected)
    assert all('千円' in p and '事業費' in p and '国費' in p for p in selected)
    totals=[]
    for p in selected:
        matches=re.findall(r'計\s+([\d,]+)\s+([\d,]+)',p)
        assert len(matches)==1
        total=[int(v.replace(',','')) for v in matches[0]]
        items=[]
        for line in p.splitlines():
            match=re.search(r'([\d,]+)\s+([\d,]+)\s*$',line)
            if match and '計' not in line:
                values=[int(v.replace(',','')) for v in match.groups()]
                if all(v>=1000 for v in values):items.append(values)
        assert [sum(v[c] for v in items) for c in [0,1]]==total
        totals.append(total)
    amounts=[totals[0], [sum(v[c] for v in totals[1:]) for c in [0,1]]]
    checks=[]
    for c,(business,national) in enumerate(amounts):
        image=table['values']['東京都'][c]
        assert (business+500)//1000==image and (national+500)//1000!=image
        checks.append(dict(year=year,prefecture='東京都',column=table['columns'][c],pdf_pages=[TOKYO_PAGES[year][0]] if c==0 else TOKYO_PAGES[year][1:],
            business_cost_thousand_yen=business,national_cost_thousand_yen=national,image_amount_million_yen=image,
            status='別県別箇所原本の事業費列と丸め一致。国費列とは異なる。',scope='東京都の当該2列に限定。全県の負担率へ外挿しない。'))
    return checks,'\f'.join(selected).strip()


def source(sid,url,title,text,locator,year):
    return dict(id=sid,title=title,url=url,kind='予算・地域配分資料',accessed=CHECKED_AT,published=PUBLISHED[year],
        published_date_evidence='公式当初配分発表日、予算ページの当初実施箇所掲載回。現行県別原本の更新日は別途未確定。',document_date=f'{year}-04',document_date_precision='month',
        locator=locator,retrieved_via='既存プロキシとTLS検証を維持した公式原本。画像2回独立転記・セルOCR、総括と県別箇所の列を分離照合。',
        sha256_extracted_text=hashlib.sha256(text.encode()).hexdigest(),excerpt=text)


def build(review,ledger):
    sources,originals,rows,scope_checks,local_checks=[],[],[],[],[]
    tables={t['year']:t for t in review['tables']}
    prior=json.loads(PRIOR.read_text())
    for year,t in tables.items():
        receipt=historical_receipt(t['url'],OUT);assert receipt['sha256_original']==t['sha256_original']
        entry=original_path(ENTRIES[year]).read_text()
        assert ('令和7年4月1日' if year==2025 else '令和8年4月7日') in entry and t['url'].split('/')[-1] in entry
        pages=pdf_pages(t['url']);overview=pages[2]
        match=re.search(r'２．配分事業費\s+([\d,]+)\s*億円',overview);assert match
        overview_amount=int(match[1].replace(',',''))
        direct,subsidy,grand=(671567,453310,1124877) if year==2025 else (684828,476622,1161450)
        assert direct+subsidy==grand and (grand+50)//100==overview_amount
        old_t=next(x for x in json.loads((ROOT/'data/mlit-water-image-review.json').read_text())['tables'] if x['year']==year)
        assert old_t['published_direct_totals'][4]==direct
        # Supplementary row totals use all seven leaf columns: river/dam/sabo/coast,
        # joint utilities/waterworks/sewer, plus the separate sewer corporation.
        other=[26601,5622] if year==2025 else [26967,6030]
        sum_leaf=sum(old_t['published_subsidy_totals'])+sum(other)+sum(t['published_totals'])+old_t['nonprefecture_sewer_business_cost']
        assert abs(sum_leaf-subsidy)<=4
        quote='直轄の配分額は工事諸費を除いた事業費を記載。'
        scope_checks.append(dict(year=year,overview_pdf_page=3,summary_pdf_page=4,overview_financial_label='配分事業費',overview_amount_hundred_million=overview_amount,
            published_direct_total_million=direct,published_subsidy_total_million=subsidy,published_grand_total_million=grand,
            published_subsidy_leaf_sum_million=sum_leaf,summary_note_quote=quote,
            finding='配分事業費の総額と直轄・補助の総括及び補助内訳が丸め一致。補助を事業費として扱う根拠。工事諸費除外の注記は直轄に限定され、補助へ適用しない。'))
        text=f'{year}年度水管理・国土保全局 当初補助配分（百万円）\n上下水道／水道\n'
        text+='\n'.join(p+'：'+'／'.join('−' if v is None else str(v) for v in vs) for p,vs in t['values'].items())
        text+=f'\n原表小計：{t["published_totals"]}\n\nPDF3頁：２．配分事業費 {overview_amount:,}億円。\nPDF4頁の直轄・補助合計：{grand:,}百万円（直轄{direct:,}＋補助{subsidy:,}）。\nPDF4頁注1：{quote}\n補助の工事諸費扱いは未確認。上下水道は独立列で水道＋下水道の親総額としない。\n'
        text+=f'表外の国費：水道施設整備費補助{t["excluded_waterworks_national_amounts"]["工事費補助"]}百万円、水道水源開発施設整備費補助{t["excluded_waterworks_national_amounts"]["施設整備費補助"]}百万円。掲載配分へ合算しない。'
        text+='\n'
        sid=f'mlit-water-utilities-{year}'
        sources.append(source(sid,t['url'],f'{year}年度水管理補助の上下水道・水道配分と金額範囲の確認',text,f'PDF3〜4頁の配分事業費・総括・直轄限定注、PDF{t["pdf_page"]}頁の上下水道・水道列。',year))
        (SOURCE_TEXT/f'mlit-water-utilities-{year}.txt').write_text(text)
        index=original_path(INDEX[year]).read_text()
        assert TOKYO[year].replace('https://www.mlit.go.jp','') in index and ('令和７年度' if year==2025 else '令和8年度') in index
        index_text=f'{year}年度事業実施箇所（当初配分） 都道府県別公式索引\n東京都 → {TOKYO[year]}\n県コードや前年度番号からURLを推定せず、東京都の掲載リンクを原本確認。'
        index_sid=f'mlit-water-utilities-index-{year}'
        sources.append(source(index_sid,INDEX[year],f'{year}年度当初実施箇所の公式県別索引',index_text,'HTMLの東京都リンク・当初配分・年度',year))
        originals.append(dict(**historical_receipt(INDEX[year],OUT),source_ids=[index_sid],verification=dict(status='retrieved',checked_at=CHECKED_AT,method='年度・当初配分・東京都のリンクを確認。',locator='HTML',row_ids=[],fields=[])))
        checked,tokyo_text=tokyo_checks(year,t);local_checks.extend(checked)
        tokyo_text+='\n'
        tokyo_sid=f'mlit-water-utilities-tokyo-{year}'
        sources.append(source(tokyo_sid,TOKYO[year],f'{year}年度東京都の上下水道・水道事業費と国費の別列',tokyo_text,'PDF'+ '/'.join(map(str,TOKYO_PAGES[year]))+'頁、単位千円・事業費列と国費列。事業別和と計を別検算。',year))
        (SOURCE_TEXT/f'mlit-water-utilities-tokyo-{year}.txt').write_text(tokyo_text)
        originals.append(dict(**historical_receipt(TOKYO[year],OUT),source_ids=[tokyo_sid],verification=dict(status='retrieved',checked_at=CHECKED_AT,method='事業費・国費の別列、年度・地域・単位を確認。事業行和と表計を照合し、事業費の百万円丸めが東京都画像2列に一致。',locator=sources[-1]['locator'],row_ids=[],fields=[])))
    for region in tables[2025]['values']:
        for c,label in enumerate(['上下水道','水道']):
            a,b=[tables[y]['values'][region][c] for y in [2025,2026]]
            note='配分事業費総額、直轄・補助総括、補助各列の小計を原本で突合。国費・交付決定・執行額に読み替えず、表外国費・災害復旧・国庫債務負担行為・全国/親総額・一括配分内数を合算しない。工事諸費除外の注記は直轄に限定され、補助の工事諸費の扱いは未確認。全県の国費/地方負担率、会計分解、個別申請・進捗・決定時点の関係者は未照合。'
            if label=='上下水道':note+='上下水道は原表の独立した配分列であり、水道＋下水道の親総額とは扱わない。'
            if region=='東京都':note+='東京都の別箇所原本で両年の事業費列に一致し、国費列とは異なることを確認。全県の負担率へ外挿しない。'
            row=dict(id=f'mlit-water-utilities-{label}-{region}',ministry='国土交通省',program=f'{label}関係補助事業（事業費・国費ではない）',
                region=region,prefecture=region,basis='当初配分（事業費）',account='会計別未分解',unit='百万円',amount2025=a,amount2026=b,
                source_ids=[f'mlit-water-utilities-{y}' for y in [2025,2026]],period2025='2025年度当初配分',period2026='2026年度当初配分',
                evidence_status='公式原本照合済み',comparability='同範囲' if a is not None and b is not None else '原本ダッシュを含む',
                scope='都道府県別の当該補助事業配分（受取自治体だけではない）',precision='百万円・四捨五入。ダッシュはnullで保持しゼロ化しない。',note=note)
            for year,amount in [(2025,a),(2026,b)]:
                row[f'amount_status{year}']='原本数値照合済み' if amount is not None else '原本ダッシュ・ゼロ認定なし'
                row[f'source_locator{year}']=dict(page=tables[year]['pdf_page'],printed_label=re.sub('[都府県]$','',region),region_row_ordinal=list(tables[year]['values']).index(region)+1,column=label)
            rows.append(row)
    for year in [2025,2026]:
        originals.append(dict(**historical_receipt(tables[year]['url'],OUT),source_ids=[f'mlit-water-utilities-{year}'],verification=dict(status='matched',checked_at=CHECKED_AT,
            method='県名付き2回の独立画像転記、全188セルOCR、原表小計と丸め、配分事業費総額・直轄/補助総括の一致を確認。工事諸費除外は直轄限定として扱う。',locator=f'PDF3〜4/{tables[year]["pdf_page"]}頁',row_ids=[r['id'] for r in rows],fields=[f'amount{year}'],values={r['id']:{f'amount{year}':r[f'amount{year}']} for r in rows})))
    enrichments=[]
    for old in prior['rows']:
        if '-subsidy-' not in old['id']:continue
        updated_note=old['note'].replace('工事諸費を除く事業費であり国費・交付決定・支出済額ではない。','配分事業費総額と補助総括・小計に一致する事業費であり国費・交付決定・支出済額ではない。')
        updated_note+='工事諸費除外の注記は直轄に限定され、補助の工事諸費の扱いは未確認。全県の国費/地方負担率は未照合。'
        enrichments.append(dict(row_id=old['id'],prior_scope_fields={k:old[k] for k in ['program','basis','comparability','note']},updated_scope_fields={'note':updated_note},source_ids=[f'mlit-water-utilities-{y}' for y in [2025,2026]]))
    report=dict(schema_version=1,checked_at=CHECKED_AT,sources=sources,originals=originals,rows=rows,row_scope_enrichments=enrichments,
        financial_scope_checks=scope_checks,tokyo_financial_basis_checks=local_checks,
        research_notes=[dict(ministry='国土交通省',status='水道・上下水道補助地域配分を原本照合',source_ids=[s['id'] for s in sources],note='47県×上下水道・水道の94行を独立画像転記とセルOCRで原本照合、76両年非欠損・18行ダッシュ。配分事業費総額と直轄/補助総括を照合し、事業費と国費を分離。東京都2列は別箇所原本の事業費列と両年一致。工事諸費除外は直轄限定で、既存141補助行の過剰な注記を証跡を残して修正。全県の負担率・個別箇所集計・実支出は未照合。')],
        data_quality_findings=[dict(id='mlit-water-subsidy-work-overhead-scope',ministry='国土交通省',title='補助事業への工事諸費除外の注記適用を訂正',status='直轄限定の注記を補助へ拡張しない',source_ids=[f'mlit-water-utilities-{y}' for y in [2025,2026]],
            note='総括表注1は「直轄の配分額は工事諸費を除いた事業費を記載」。既存141補助行の工事諸費除外という断定だけを訂正し、元の宣言・数値・原本照合証跡を保持。配分事業費という全体の明示、総括・内訳の一致と東京都事業費列の一致は事業費の根拠として保存。補助の工事諸費扱い・全県負担率は未確認。')],
        verification_summary=dict(new_rows=len(rows),fully_nonmissing_rows=sum(r['amount2025'] is not None and r['amount2026'] is not None for r in rows),rows_with_original_dash=sum(r['amount2025'] is None or r['amount2026'] is None for r in rows),reviewed_cells=len(ledger['cells']),ocr_exact_matches=sum(c['match'] for c in ledger['cells']),scope_note_corrections=len(enrichments),new_original_urls=2,
            primary_review_sha256=digest(REVIEW),independent_review_sha256=digest(INDEPENDENT),ocr_ledger_sha256=digest(OCR)),
        limitations=['独立画像転記とOCRは同一原本の再照合であり、別発行原本の全県集計ではない。','東京都だけ別県別箇所原本の事業費/国費列を突合。全県の負担率へ外挿しない。','同じ当初表の同じ列を比較。対象箇所・制度変更・申請と決定時点の関係者・地域別執行は別調査。'])
    validate_report(report)
    return report


def validate_report(report):
    """Cache-free checks for integration; the original verifiers are separate."""
    review,ledger=read_review()
    tables={t['year']:t for t in review['tables']}
    ids={s['id'] for s in report['sources']};prior={r['id']:r for r in json.loads(PRIOR.read_text())['rows']}
    assert len(report['rows'])==94 and len(report['row_scope_enrichments'])==141
    assert sum(r['amount2025'] is not None and r['amount2026'] is not None for r in report['rows'])==76
    assert report['verification_summary']['primary_review_sha256']==digest(REVIEW)
    assert report['verification_summary']['independent_review_sha256']==digest(INDEPENDENT)
    assert report['verification_summary']['ocr_ledger_sha256']==digest(OCR)
    for r in report['rows']:
        assert set(r['source_ids'])<=ids and r['basis']=='当初配分（事業費）'
        if r['amount2025'] is not None and r['amount2026'] is not None:
            assert r['comparability']=='同範囲'
        else:
            assert r['comparability']=='原本ダッシュを含む'
        column=0 if r['program'].startswith('上下水道') else 1
        for year in [2025,2026]:
            assert r[f'amount{year}']==tables[year]['values'][r['prefecture']][column]
    for e in report['row_scope_enrichments']:
        assert set(e['source_ids'])<=ids and e['updated_scope_fields'].keys()=={'note'}
        for key,value in e['prior_scope_fields'].items():assert prior[e['row_id']][key]==value
        assert '工事諸費を除く事業費' not in e['updated_scope_fields']['note'] and '直轄に限定' in e['updated_scope_fields']['note']
    for original in report['originals']:
        assert original['url'].startswith('https://www.mlit.go.jp/') and set(original['source_ids'])<=ids
        if original['verification']['status']=='matched':assert set(original['verification']['row_ids'])=={r['id'] for r in report['rows']}
    assert all(s['sha256_extracted_text']==hashlib.sha256(s['excerpt'].encode()).hexdigest() for s in report['sources'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for flag in ['fetch','write','ocr','validate-only']:parser.add_argument('--'+flag,action='store_true')
    args=parser.parse_args()
    if args.validate_only:
        report=json.loads(OUT.read_text());validate_report(report);print(json.dumps(report['verification_summary'],ensure_ascii=False));return
    review,ledger=read_review()
    if args.fetch:
        for url in [*[t['url'] for t in review['tables']],*ENTRIES.values(),*INDEX.values(),*TOKYO.values()]:
            try:fetch(url)
            except (HTTPError,URLError,TimeoutError,ValueError) as error:print(json.dumps(dict(url=url,status='blocked_or_failed',**failure_details(error)),ensure_ascii=False))
    if args.ocr:
        old={(c['year'],c['family'],c['region'],c['column']):c for c in ledger['cells']}
        with ThreadPoolExecutor(max_workers=4) as pool:cells=list(pool.map(ocr_cell,crop_jobs(review)))
        for c in cells:
            base=old[(c['year'],c['family'],c['region'],c['column'])]
            if all(c[k]==base[k] for k in ['sha256_cell_png','ocr_raw','reviewed','ocr']):
                for key in ['visual_review_status','visual_review_value','visual_review_reason']:
                    if key in base:c[key]=base[key]
        (CACHE/'ocr-candidate.json').write_text(json.dumps(cells,ensure_ascii=False,indent=2)+'\n')
        assert {(c['year'],c['family'],c['region'],c['column']):c for c in cells}==old,'Changed OCR candidate requires review'
    report=build(review,ledger)
    if args.write:OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    else:assert report==json.loads(OUT.read_text()),'Changed report requires review'
    print(json.dumps(report['verification_summary'],ensure_ascii=False))


if __name__=='__main__':main()
