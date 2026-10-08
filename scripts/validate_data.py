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

ROOT=Path(__file__).resolve().parents[1]
d=json.loads((ROOT/'public/data.json').read_text())
assert d['schema_version']==1
assert len(d['prefectures'])==47 and len(set(d['prefectures']))==47
sources={s['id']:s for s in d['sources']}
assert len(sources)==len(d['sources'])
profiles=reviewed_profiles(ROOT/'data/source-text')
expansion=reviewed_expansion(ROOT/'data/source-text')
followup=reviewed_followup(ROOT/'data/source-text')
approved_party_urls={url for party,url in PARTIES.values()}
approved_party_urls.update(e['url'] for e in profiles['sources'])
approved_party_urls.update(e['url'] for e in expansion['sources'])
approved_party_urls.update(e['url'] for e in followup['sources'])
for s in sources.values():
    u=urlsplit(s['url'])
    assert u.scheme=='https' and u.hostname and not u.username and not u.password
    if s['kind']=='所属党':
        assert s['url'] in approved_party_urls,s['url']
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
    if r['basis']=='執行額（4〜7月累計）':
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
    assert (m['name'],m['chamber'],m['district'])==(e['member_name'],e['chamber'],e['district'])
    assert any(p['source_id']==e['source_id'] and p['party']==e['party'] and p['as_of'] is None
               for p in m['party_evidence'])
for e in expansion['entries']:
    m=members_by_id[e['member_id']]
    assert (m['name'],m['chamber'],m['district'])==(e['member_name'],e['chamber'],e['district'])
    assert any(p['source_id']==e['source_id'] and p['party']==e['party'] and p['as_of'] is None
               and p['checked_at']==e['checked_at'] and p['original_location']==e['original_location'] for p in m['party_evidence'])
validate_party_followup()
validate_municipality_districts()
print(f"Data verified: {len(d['rows'])} rows, {len(d['sources'])} official sources, {len(d['legislators'])} legislators. National totals reconciled; rounded regional totals checked.")
