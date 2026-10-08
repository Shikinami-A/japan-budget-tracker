"""Check sources, reconciliation, missingness, and public artifact boundaries."""
import hashlib
import json
import math
import re
from pathlib import Path
from urllib.parse import urlsplit
from party_sources import PARTIES, reviewed_profiles, reviewed_expansion
from verify_party_followup import registry as reviewed_followup
from verify_party_followup import validate as validate_party_followup
from verify_municipality_districts import validate as validate_municipality_districts
from verify_special_execution import validate as validate_special_execution
from verify_roster_followup import validate as validate_roster_followup
from verify_party_third_stage import validate as validate_party_third_stage
from verify_roster_continued import validate as validate_roster_continued
from verify_fdma_criteria import validate as validate_fdma_criteria
from verify_fdma_reconciliation import validate as validate_fdma_reconciliation
from verify_caa_execution import validate_report as validate_caa_execution
from verify_caa_execution import validate_scope_report
from verify_party_fourth_stage import validate as validate_party_fourth_stage
from verify_mlit_water_utilities import validate_report as validate_water_utilities
from verify_mlit_water_images import read_review as read_water_review, verify_ocr as validate_water_ocr

ROOT=Path(__file__).resolve().parents[1]
validate_fdma_criteria(json.loads((ROOT/'data/reviewed-fdma-criteria.json').read_text()))
validate_fdma_reconciliation()
validate_caa_execution(json.loads((ROOT/'data/reviewed-caa-execution.json').read_text()))
validate_scope_report(json.loads((ROOT/'data/other-ministry-scope-review.json').read_text()))
validate_party_fourth_stage()
validate_water_utilities(json.loads((ROOT/'data/reviewed-mlit-water-utilities.json').read_text()))
d=json.loads((ROOT/'public/data.json').read_text())
assert d['schema_version']==1
assert len(d['prefectures'])==47 and len(set(d['prefectures']))==47
sources={s['id']:s for s in d['sources']}
assert len(sources)==len(d['sources'])
profiles=reviewed_profiles(ROOT/'data/source-text')
expansion=reviewed_expansion(ROOT/'data/source-text')
followup=reviewed_followup(ROOT/'data/source-text')
third_stage=json.loads((ROOT/'data/reviewed-party-third-stage.json').read_text())
continued=json.loads((ROOT/'data/reviewed-roster-continued.json').read_text())
continued_urls={s['id']:s['url'] for s in continued['sources']}
fourth_stage=json.loads((ROOT/'data/reviewed-party-fourth-stage.json').read_text())
continued_urls.update({s['id']:s['url'] for s in fourth_stage['sources']})
approved_party_urls={url for party,url in PARTIES.values()}
approved_party_urls.update(e['url'] for e in profiles['sources'])
approved_party_urls.update(e['url'] for e in expansion['sources'])
approved_party_urls.update(e['url'] for e in followup['sources'])
approved_party_urls.update(e['url'] for e in third_stage['sources'])
approved_party_urls.update(continued_urls.values())
for s in sources.values():
    u=urlsplit(s['url'])
    assert u.scheme=='https' and u.hostname and not u.username and not u.password
    if s['kind']=='所属党':
        assert s['url'] in approved_party_urls,s['url']
    elif s['id'] in continued_urls:
        assert s['url']==continued_urls[s['id']]
    else:
        assert u.hostname.endswith(('.go.jp','.lg.jp')) or u.hostname=='www.pref.aichi.jp', s['url']
    assert hashlib.sha256(s['excerpt'].encode()).hexdigest()==s['sha256_extracted_text']
    if 'original' in s:
        o=s['original']
        assert o['url']==s['url'] and o['status']=='downloaded'
        assert re.fullmatch(r'[0-9a-f]{64}',o['sha256_original'])
        assert o['sha256_original']!=s['sha256_extracted_text']
        assert isinstance(o['bytes'],int) and o['bytes']>0
        assert o['retrieved_at_utc'] and o['final_url'].startswith('https://')
assert len({r['id'] for r in d['rows']})==len(d['rows'])
for r in d['rows']:
    assert r['unit']=='百万円'
    assert r['source_ids'] and all(i in sources for i in r['source_ids'])
    assert all(i in sources for i in r.get('explanation_source_ids',[]))
    assert all(i in sources for i in r.get('scope_review_source_ids',[]))
    for check in r.get('driver_checks', []):
        assert all(i in sources for i in check['source_ids'])
        assert isinstance(check['status'], str) and check['status'] and check['finding']
    for field in ['amount2025','amount2026']:
        assert r[field] is None or isinstance(r[field],(float,int)) and math.isfinite(r[field]) and r[field]>=0
    if r['comparability']=='同範囲':
        assert r['amount2025'] is not None and r['amount2026'] is not None
    if r['prefecture'] is not None: assert r['prefecture'] in d['prefectures']
    assert r.get('legislator_mapping_status')
    for year in (2025, 2026):
        assert r.get(f'amount_status{year}')
    if r.get('municipality_mapping'):
        mapping=r['municipality_mapping']
        assert mapping in d['municipality_mappings'] and mapping['prefecture']==r['prefecture']
        assert mapping['municipality']==r['region'].removeprefix(r['prefecture'])
        assert mapping['source_id'] in sources
        if mapping['boundary_source_id']: assert mapping['boundary_source_id'] in sources
    verified=set()
    for sid in r['source_ids']:
        for v in sources[sid].get('original',{}).get('verifications',[]):
            if v['status']=='matched' and r['id'] in v['row_ids']:verified.update(v['fields'])
    assert set(r.get('original_verified_fields',[]))==verified & {'amount2025','amount2026'},r['id']
    if r['evidence_status']=='原本数値照合済み':
        assert {'amount2025','amount2026'}<=verified and r['amount2025'] is not None and r['amount2026'] is not None,r['id']
    if r['basis']=='当初配分（事業費）':
        assert '事業費' in r['program'] and '国費' in r['note']
        assert r['account']=='会計別未分解'
        if r['scope'].startswith('地方支分部局'):assert r['prefecture'] is None

caa_rows=[r for r in d['rows'] if r.get('agency')=='消費者庁' and r['program']=='地方消費者行政強化交付金']
assert len(caa_rows)==47
for r in caa_rows:
    assert r['ministry']=='内閣府' and r['amount2026'] is None
    assert r['original_verified_fields']==['amount2025']
    assert r['comparability']!='同範囲' and r['view_group']=='reference'

assert d['original_coverage']['sources_with_original']==sum('original' in s for s in sources.values())
assert d['original_coverage']['fully_verified_comparison_rows']==sum(r['evidence_status']=='原本数値照合済み' for r in d['rows'])

def reconcile(basis, field, expected, tolerance=0.002):
    rows=[r for r in d['rows'] if r['region']=='全国' and r['account']=='一般会計' and r['basis']==basis]
    actual=sum(r[field] for r in rows)
    assert abs(actual-expected)<=tolerance,(basis,field,actual,expected)
    return len(rows)
assert reconcile('当初予算','amount2025',115197845.248)==19
assert reconcile('当初予算','amount2026',122309247.035)==19
# Item subtotals must exhaust each ministry, without adding the parent totals.
items=[r for r in d['rows'] if r.get('view_group')=='programs']
assert len(items)==859 and len({r['ministry'] for r in items})==19
assert sum(r['amount2025'] is not None and r['amount2026'] is not None for r in items)==771
for year in (2025,2026):
    for parent in [r for r in d['rows'] if r['basis']=='当初予算' and r['account']=='一般会計']:
        children=[r for r in items if r['ministry']==parent['ministry']]
        actual=sum(r[f'amount_thousand_yen{year}'] or 0 for r in children)
        assert round(parent[f'amount{year}']*1000)==actual,(year,parent['ministry'])
    for r in items:
        assert r['prefecture'] is None and r['region']=='全国'
        assert r['comparability']!='同範囲'
        value=r[f'amount{year}'];exact=r[f'amount_thousand_yen{year}']
        assert (value is None and exact is None) or value is not None and round(value*1000)==exact
institutions=[r for r in d['rows'] if r.get('view_group')=='institutions']
assert len(institutions)==86
for year,total in [(2025,1078350085),(2026,1097136487)]:
    assert sum(round(r[f'amount{year}']*1000) for r in institutions)==total
assert all(r['prefecture'] is None and r['region']=='全国' and r.get('geography_status') for r in institutions)
assert sum(r.get('institution') is not None for r in institutions)==85
for year,total,count in [(2025,10039796,214),(2026,9699402,198)]:
    rows=[r for r in d['rows'] if r['program']=='子ども・子育て支援施設整備交付金']
    assert len(rows)==330 and sum(r[f'amount{year}'] is not None for r in rows)==count
    assert sum(round(r[f'amount{year}']*1000) for r in rows if r[f'amount{year}'] is not None)==total
    assert all(r['view_group']=='reference' and r['comparability']!='同範囲' for r in rows)
for year,total in [(2025,135995055),(2026,35123366)]:
    rows=[r for r in d['rows'] if r['ministry']=='環境省' and r.get('view_group')=='reference']
    assert len(rows)==47 and sum(round(r[f'amount{year}']*1000) for r in rows)==total
    assert all(r['comparability']!='同範囲' for r in rows)
for c in d['coverage']:
    assert all(all(sid in sources for sid in n['source_ids']) for n in c.get('research_notes',[]))
# Each ministry and the overall total are independently truncated below 1,000 yen.
assert reconcile('執行額（4〜6月）','amount2025',36532534.837,0.020)==18
assert reconcile('執行額（4〜6月）','amount2026',39047272.046,0.020)==18
assert reconcile('執行額（4〜7月累計）','amount2025',42254458.573,0.020)==18
assert reconcile('執行額（4〜7月累計）','amount2026',45665915.744,0.020)==18
for r in d['rows']:
    if r['basis']=='執行額（4〜7月累計）' and r['account']=='一般会計':
        earlier=next(p for p in d['rows'] if p['id']==f'q1-{r["ministry"]}')
        assert all(r[f'amount{year}']>=earlier[f'amount{year}'] for year in [2025,2026])
for scope, year, expected in [('道府県分',2025,9272243),('市町村分合計',2025,8547545),('道府県分',2026,10103999),('市町村分合計',2026,8869736)]:
    rows=[r for r in d['rows'] if r['program']=='普通交付税' and r['scope']==scope]
    assert len(rows)==47
    assert abs(sum(r[f'amount{year}'] for r in rows)-expected)<=24
for year,expected,count in [(2025,11993,120),(2026,12119,122)]:
    rows=[r for r in d['rows'] if r['program']=='特定防衛施設周辺整備調整交付金' and r[f'amount{year}'] is not None]
    assert len(rows)==count,(year,len(rows),count)
    assert abs(sum(r[f'amount{year}'] for r in rows)-expected)<=count/2
assert len({m['id'] for m in d['legislators']})==len(d['legislators'])
for m in d['legislators']:
    assert all(p in d['prefectures'] for p in m['prefectures'])
    if m['party'] is not None:
        assert m['party_source'] and m['party_checked_at'] and m['party_evidence']
        assert {e['party'] for e in m['party_evidence']}=={m['party']}
    if m['party_status']=='資料間不一致':assert m['party'] is None and len({e['party'] for e in m['party_evidence']})>1
    assert all(e.get('source_id') is None or e['source_id'] in sources for e in m['party_evidence'])
    if m['election_type']=='比例代表': assert m['prefectures']==[]
assert d['party_coverage']['verified']==sum(m['party'] is not None for m in d['legislators'])
assert len({o['id'] for o in d.get('research_observations', [])})==len(d.get('research_observations', []))
for o in d.get('research_observations', []):
    assert not o['is_annual_total']
    assert o['source_ids'] and all(sid in sources for sid in o['source_ids'])
    for key in ('national_cost_million_yen', 'business_cost_million_yen'):
        assert o[key] is None or math.isfinite(o[key]) and o[key] >= 0
    if o['notification_round'] in (64, 69): assert o['fiscal_year'] is None
    if o['notification_round'] == 68: assert o['fiscal_year'] == 2026
members_by_id={m['id']:m for m in d['legislators']}
for e in profiles['entries']:
    m=members_by_id[e['member_id']]
    assert (m['name'],m['chamber'],m.get('baseline_roster',m)['district'])==(e['member_name'],e['chamber'],e['district'])
    assert any(p['source_id']==e['source_id'] and p['party']==e['party'] and p['as_of'] is None
               for p in m['party_evidence'])
for e in expansion['entries']:
    m=members_by_id[e['member_id']]
    assert (m['name'],m['chamber'],m.get('baseline_roster',m)['district'])==(e['member_name'],e['chamber'],e['district'])
    assert any(p['source_id']==e['source_id'] and p['party']==e['party'] and p['as_of'] is None
               and p['checked_at']==e['checked_at'] and p['original_location']==e['original_location'] for p in m['party_evidence'])
validate_party_followup()
validate_municipality_districts()
special_execution=json.loads((ROOT/'data/reviewed-special-execution.json').read_text())
validate_special_execution(special_execution)
for reviewed in special_execution['rows']:
    actual=next(r for r in d['rows'] if r['id']==reviewed['id'])
    for key in ('amount2025','amount2026','basis','account','scope','comparability'):
        assert actual[key]==reviewed[key]
drivers=json.loads((ROOT/'data/reviewed-national-drivers.json').read_text())
for check in drivers['numerical_driver_checks']:
    actual=next(r for r in d['rows'] if r['id']==check['row_id'])
    for year in (2025,2026):
        field=f'amount_thousand_yen{year}'
        assert sum(leaf[field] or 0 for leaf in check['leaf_details'])==check[field]
        assert round(actual[f'amount{year}']*1000)==check[field]
    assert check['delta_thousand_yen']==check['amount_thousand_yen2026']-check['amount_thousand_yen2025']
    assert all(sid in sources for sid in check['source_ids'])
validate_roster_followup()
validate_party_third_stage()
validate_roster_continued()
for filename in ('reviewed-care-cities.json', 'reviewed-fdma-facilities.json', 'reviewed-reconstruction-followup.json'):
    report=json.loads((ROOT/'data'/filename).read_text())
    for reviewed in report['rows']:
        actual=next(r for r in d['rows'] if r['id']==reviewed['id'])
        for key in ('amount2025','amount2026','ministry','program','basis','account','scope','comparability'):
            assert actual[key]==reviewed[key],(filename,reviewed['id'],key)
        assert actual['view_group']=='reference' and actual['comparability']!='同範囲'
care_cities=json.loads((ROOT/'data/reviewed-care-cities.json').read_text())
assert len(care_cities['rows'])==82
for year in (2025,2026):
    reconciliation=care_cities['reconciliation'][str(year)]
    for group in ('指定都市','中核市'):
        rows=[r for r in care_cities['rows'] if r['scope']==f'{group}分（県分とは別枠）']
        assert len(rows)==(20 if group=='指定都市' else 62)
        assert sum(round(r[f'amount{year}']*1000) for r in rows)==reconciliation['subtotals'][group]['amount_thousand_yen']
        assert sum(r[f'plans{year}'] for r in rows)==reconciliation['subtotals'][group]['plans']
    assert reconciliation['total']['amount_thousand_yen']==(6214838 if year==2025 else 6873286)
fdma=json.loads((ROOT/'data/reviewed-fdma-facilities.json').read_text())
assert len(fdma['rows'])==124
assert sum(r['amount2025'] is None or r['amount2026'] is None for r in fdma['rows'])==84
for year,count,total in ((2025,91,1278929),(2026,73,1108214)):
    present=[r for r in fdma['rows'] if r[f'amount{year}'] is not None]
    assert len(present)==count and sum(round(r[f'amount{year}']*1000) for r in present)==total
    assert fdma['reconciliation'][str(year)]['total']['recipient_sum_thousand_yen']==total
plan=json.loads((ROOT/'data/reviewed-reconstruction-followup.json').read_text())
for check in plan['plan_checks']:
    assert sum(check['annual_component_amounts_thousand_yen'])==check['business_cost_thousand_yen']
    assert check['published_plan_grant_thousand_yen']*2==check['business_cost_thousand_yen']
assert plan['interim_remainder_assessment']['status']=='丸め値の突合一致・排他性未確定'
assert all(o['is_cash_spending'] is False for o in plan['contract_progress_observations'])
assert d['held_roster_records']==continued['held_records']
assert d['service_end_count']==sum(m.get('current_roster_eligible') is False for m in d['legislators'])==1
ended=members_by_id['house-渡辺孝一']
assert ended['current_roster_eligible'] is False and ended['service_end_date']=='2026-09-17'
assert all(e['source_id'] in sources for e in ended['service_evidence'])
for e in continued['party_entries']:
    assert any(p['source_id']==e['source_id'] and p['party']==e['party'] and p['as_of'] is None
               for p in members_by_id[e['member_id']]['party_evidence'])
water_review=read_water_review()
water_ocr=json.loads((ROOT/'data/mlit-water-image-ocr.json').read_text())
validate_water_ocr(water_review,water_ocr)
water=json.loads((ROOT/'data/reviewed-mlit-water-images.json').read_text())
assert len(water['rows'])==181 and water['verification_summary']['fully_nonmissing_rows']==173
assert sum(r['amount2025'] is None or r['amount2026'] is None for r in water['rows'])==8
for name,key in (('mlit-water-image-review.json','review_sha256'),('mlit-water-image-ocr.json','ocr_ledger_sha256'),('mlit-water-independent-review.json','independent_review_sha256')):
    assert hashlib.sha256((ROOT/'data'/name).read_bytes()).hexdigest()==water['verification_summary'][key]
for reviewed in water['rows']:
    actual=next(r for r in d['rows'] if r['id']==reviewed['id'])
    assert actual['view_group'] is None
    for key in ('amount2025','amount2026','region','prefecture','comparability','source_locator2025','source_locator2026'):
        assert actual[key]==reviewed[key]
    for year in (2025,2026):
        table=next(t for t in water_review['tables'] if t['year']==year)
        family='direct' if '-direct-' in reviewed['id'] else 'subsidy'
        column=table[f'{family}_columns'].index(reviewed[f'source_locator{year}']['column'])
        assert reviewed[f'amount{year}']==table[family][reviewed['region']][column]
print(f"Data verified: {len(d['rows'])} rows, {len(d['sources'])} official sources, {len(d['legislators'])} legislators. National totals reconciled; rounded regional totals checked.")
