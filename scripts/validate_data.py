"""Check sources, reconciliation, missingness, and public artifact boundaries."""
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
d=json.loads((ROOT/'public/data.json').read_text())
assert d['schema_version']==1
assert len(d['prefectures'])==47 and len(set(d['prefectures']))==47
sources={s['id']:s for s in d['sources']}
assert len(sources)==len(d['sources'])
for s in sources.values():
    u=urlsplit(s['url'])
    assert u.scheme=='https' and u.hostname and not u.username and not u.password
    assert u.hostname.endswith(('.go.jp','.lg.jp')) or u.hostname=='www.pref.aichi.jp', s['url']
    assert hashlib.sha256(s['excerpt'].encode()).hexdigest()==s['sha256_extracted_text']
assert len({r['id'] for r in d['rows']})==len(d['rows'])
for r in d['rows']:
    assert r['unit']=='百万円'
    assert r['source_ids'] and all(i in sources for i in r['source_ids'])
    assert all(i in sources for i in r.get('explanation_source_ids',[]))
    for field in ['amount2025','amount2026']:
        assert r[field] is None or isinstance(r[field],(float,int)) and math.isfinite(r[field]) and r[field]>=0
    if r['comparability']=='同範囲':
        assert r['amount2025'] is not None and r['amount2026'] is not None
    if r['prefecture'] is not None: assert r['prefecture'] in d['prefectures']

def reconcile(basis, field, expected, tolerance=0.002):
    rows=[r for r in d['rows'] if r['region']=='全国' and r['account']=='一般会計' and r['basis']==basis]
    actual=sum(r[field] for r in rows)
    assert abs(actual-expected)<=tolerance,(basis,field,actual,expected)
    return len(rows)
assert reconcile('当初予算','amount2025',115197845.248)==19
assert reconcile('当初予算','amount2026',122309247.035)==19
# Each ministry and the overall total are independently truncated below 1,000 yen.
assert reconcile('執行額（4〜6月）','amount2025',36532534.837,0.020)==18
assert reconcile('執行額（4〜6月）','amount2026',39047272.046,0.020)==18
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
    if m['party'] is not None: assert m['party_source'] and m['party_as_of']
    if m['election_type']=='比例代表': assert m['prefectures']==[]
print(f"Data verified: {len(d['rows'])} rows, {len(d['sources'])} official sources, {len(d['legislators'])} legislators. National totals reconciled; rounded regional totals checked.")
