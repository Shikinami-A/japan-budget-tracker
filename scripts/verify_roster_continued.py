"""Continue roster/party checks without replacing historical roster identities.

--fetch uses the configured proxy and default TLS verification, validates each
redirect, and records failures without retrying. Raw originals remain ignored.
Only reviewed public identity fields are committed by --generate.
"""
import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

from fetch_sources import failure_details
from verify_party_expansion import DOM, Node, compact, district_matches, ldp_entries
from verify_roster_followup import descendants

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/roster-continued'
REPORT = ROOT / 'data/reviewed-roster-continued.json'
DISTRICTS_REPORT = ROOT / 'data/reviewed-aichi-districts.json'
TARGETS = {
    'watanabe-obituary': 'https://www.jimin.jp/news/information/214223.html',
    'kurihara-profile': 'https://www.shugiin.go.jp/Internet/itdb_giinprof.nsf/html/profile/157.html',
    'cdp-tsujimoto': 'https://cdp-japan.jp/member/4357',
    'ldp-json': 'https://www.jimin.jp/member/data/member.json',
    'ldp-fujii-profile': 'https://www.jimin.jp/member/102132.html',
    'sdp-members': 'https://sdp.or.jp/member/',
    'senate-roster': 'https://www.sangiin.go.jp/japanese/giin/hireiku/hireiku.htm',
    'aichi-boundaries': 'https://www.pref.aichi.jp/soshiki/senkyo/0000022887.html',
    'mic-boundaries': 'https://www.soumu.go.jp/senkyo/senkyo_s/news/senkyo/shu_kuwari/shu_kuwari_4.html',
    'mic-aichi-boundaries': 'https://www.soumu.go.jp/main_content/000853828.pdf',
    'craj-foundation': 'https://craj.jp/news/20260122_0038',
}
HOSTS = {urlsplit(u).hostname for u in TARGETS.values()}
MAX_BYTES = 4_000_000
CHECKED_AT = '2026-10-09'


def checked(url):
    p = urlsplit(url)
    assert p.scheme == 'https' and p.hostname in HOSTS
    assert not p.username and not p.password and p.port in (None, 443)
    return url


class Redirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        return super().redirect_request(request, response, code, message, headers, checked(url))


def fetch(keys):
    policy = json.loads(Path('/etc/codex/network-policy.json').read_text())['http_network_policy']
    hosts = {urlsplit(TARGETS[k]).hostname for k in keys}
    assert policy['type'] == 'unrestricted' or hosts <= {r['host'] for r in policy['egress_rules']}
    CACHE.mkdir(parents=True, exist_ok=True)
    for key in keys:
        url = checked(TARGETS[key])
        at = datetime.now(timezone.utc).isoformat()
        try:
            with build_opener(Redirect()).open(url, timeout=30) as response:
                raw = response.read(MAX_BYTES + 1)
                assert 0 < len(raw) <= MAX_BYTES
                receipt = dict(source_id=key, url=url, final_url=checked(response.geturl()),
                    retrieved_at_utc=at, bytes=len(raw), sha256_original=hashlib.sha256(raw).hexdigest(),
                    content_type=response.headers.get_content_type(), download_status='downloaded')
            (CACHE / (key + '.bin')).write_bytes(raw)
        except Exception as error:
            receipt = dict(source_id=key, url=url, retrieved_at_utc=at,
                download_status='blocked_or_failed', **failure_details(error))
        # Failure receipts do not promote a previous successful cache to current.
        (CACHE / (key + '.receipt.json')).write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(dict(source_id=key, download_status=receipt['download_status']), ensure_ascii=False))


def original(key):
    receipt = json.loads((CACHE / (key + '.receipt.json')).read_text())
    assert receipt['url'] == TARGETS[key] and receipt['download_status'] == 'downloaded'
    raw = (CACHE / (key + '.bin')).read_bytes()
    assert 0 < len(raw) == receipt['bytes'] <= MAX_BYTES
    assert hashlib.sha256(raw).hexdigest() == receipt['sha256_original']
    checked(receipt['url']); checked(receipt['final_url'])
    return raw, receipt


def dom(key):
    raw, receipt = original(key)
    parser = DOM(); parser.feed(raw.decode('cp932' if key in {'kurihara-profile', 'mic-boundaries'} else 'utf-8'))
    return parser.root, receipt


def source(report, key, records, title, as_of=None):
    _, receipt = original(key)
    sid = 'roster-continued-' + key
    filename = sid + '.txt'
    text = '\n'.join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in records) + '\n'
    (ROOT / 'data/source-text' / filename).write_text(text)
    report['sources'].append(dict(id=sid, url=TARGETS[key], filename=filename, title=title,
        as_of=as_of, checked_at=CHECKED_AT, sha256=hashlib.sha256(text.encode()).hexdigest(), original=receipt,
        method='公式原本の氏名・院・選挙区・事象を独立抽出。会派は党籍に読み替えない。'))
    if receipt not in report['originals']:
        report['originals'].append(receipt)
    return sid


def senate_records(root):
    assert '令和８年９月４日現在' in root.text()
    selected = []
    for index, tr in enumerate(descendants(root, 'tr')):
        cells = [n for n in tr.children if isinstance(n, Node) and n.tag == 'td']
        if len(cells) != 6:
            continue
        name = compact(cells[1].text()).split('[')[0]
        if name not in {'辻元清美', '福島みずほ', 'ラサール石井'}:
            continue
        assert compact(cells[3].text()) == '比例'
        selected.append(dict(name=name, original_name=compact(cells[1].text()),
            reading=compact(cells[2].text()), chamber='参議院', district='全国比例',
            caucus=compact(cells[0].text()), as_of='2026-09-04',
            original_location=f'HTML table tr[{index}] cells[0:6] 会派/氏名/読み/比例/任期/正字'))
    assert len(selected) == 3
    return selected


def generate():
    members = {m['id']: m for m in json.loads((ROOT / 'public/data.json').read_text())['legislators']}
    report = dict(schema_version=1, checked_at=CHECKED_AT,
        note='旧名簿711人のID・氏名・資料日・URL・党籍根拠を保持。逝去は在職状態の別証跡、新原本のみの議員は保留観測。党資料日不明を取得日や国会原本日で補わず、不一致24人を解消しない。',
        sources=[], originals=[], member_enrichments=[], held_records=[], party_entries=[], issues=[], retrieval_attempts=[], stats={})
    failure = json.loads((CACHE / 'craj-foundation.receipt.json').read_text())
    assert failure['url'] == TARGETS['craj-foundation'] and failure['download_status'] == 'blocked_or_failed'
    report['retrieval_attempts'].append(failure)
    report['issues'].append(dict(status='取得不能', scope='中道改革連合の結党・党籍異動日',
        source_url=failure['url'], retrieval_attempt_source_id=failure['source_id'],
        reason='原本はHTTP応答403。日付不明の公明・中道資料間不一致24人を推測で解消しない。'))
    d, _ = dom('watanabe-obituary')
    t = compact(d.text())
    assert compact('渡辺孝一衆議院議員が9月17日（木）4時45分、逝去されました。') in t
    assert compact('逝去日：令和8年9月17日') in t
    obituary = dict(name='渡辺孝一', chamber='衆議院', event='逝去', event_date='2026-09-17',
        excerpt='渡辺孝一衆議院議員が9月17日（木）4時45分、逝去されました。逝去日：令和8年9月17日。',
        original_location='HTML本文の氏名・逝去日。家族名・会場・連絡先を除外')
    sid = source(report, 'watanabe-obituary', [obituary], '自民党公式：渡辺孝一衆議院議員逝去')
    m = members['house-渡辺孝一']
    assert (m['name'], m['chamber'], m['district']) == ('渡辺孝一', '衆議院', '（比）北海道')
    report['member_enrichments'].append(dict(member_id=m['id'], member_name=m['name'], chamber=m['chamber'],
        baseline_as_of=m['as_of'], baseline_source_url=m['source_url'], baseline_district=m['district'],
        service_status='旧名簿・逝去を公式確認', current_roster_eligible=False, service_end_date='2026-09-17',
        evidence=dict(source_id=sid, excerpt_line=1, url=TARGETS['watanabe-obituary'], original_location=obituary['original_location']),
        note='党公式の訃報による逝去の直接確認。現職名簿に正式名未一致という既存証跡と別に保存し、旧名簿を削除しない。予算配分決定時点の党籍の証明にはしない。'))

    ldp = ldp_entries(original('ldp-json')[0])
    selected_ldp = [r for r in ldp if r['name'] in {'栗原渉', '藤井ひさゆき'}]
    assert len(selected_ldp) == 2
    ldp_sid = source(report, 'ldp-json', selected_ldp, '自民党公式現職JSON：栗原・藤井の照合用抜粋')
    d, _ = dom('kurihara-profile')
    heading = compact([x for x in descendants(d, 'h2') if x.attrs.get('id') == 'TopContents'][0].text())
    assert heading == '栗原渉(くりはらわたる)'
    assert '小選挙区(福岡県第五区)選出' in compact(d.text())
    assert '令和8年9月18日現在' in compact(d.text())
    kurihara = dict(name='栗原渉', reading='くりはらわたる', chamber='衆議院', district='福岡5',
        as_of='2026-09-18', original_location='HTML h2#TopContents・小選挙区選出段落・現在日')
    ksid = source(report, 'kurihara-profile', [kurihara], '衆議院公式：栗原渉プロフィール', '2026-09-18')
    party = [r for r in selected_ldp if r['name'] == '栗原渉'][0]
    assert district_matches(dict(kurihara, election_type='小選挙区'), party)
    roster = json.loads((ROOT / 'data/reviewed-roster-followup.json').read_text())
    rsource = next(s for s in roster['sources'] if s['id'] == 'roster-house-roster-2-original')
    observations = [json.loads(line) for line in (ROOT / 'data/source-text' / rsource['filename']).read_text().splitlines()]
    matches = [(line, r) for line, r in enumerate(observations, 1) if r['name'] == '栗原渉']
    assert len(matches) == 1 and matches[0][1]['district'] == '福岡5'
    assert not any(m['name'] == '栗原渉' and m['chamber'] == '衆議院' for m in members.values())
    report['held_records'].append(dict(**kurihara, status='旧名簿IDに未一致・新原本独立確認',
        proposed_member_id=None, profile_evidence=dict(source_id=ksid, excerpt_line=1),
        roster_evidence=dict(source_id=rsource['id'], excerpt_line=matches[0][0], as_of=matches[0][1]['as_of']),
        party_observation=dict(party='自由民主党', source_id=ldp_sid, excerpt_line=selected_ldp.index(party)+1, as_of=None),
        note='衆議院一覧・本人プロフィール・党現職JSONで正式名・院・福岡5が一致。渡辺孝一の後継とみなさず、旧711人名簿への自動追加・ID置換はしない。'))

    senate, _ = dom('senate-roster')
    srecords = senate_records(senate)
    senate_sid = source(report, 'senate-roster', srecords, '参議院公式：追加党籍3人の氏名・全国比例確認', '2026-09-04')
    d, _ = dom('cdp-tsujimoto')
    names = d.all('member-name')[0]
    original_name = names.children[1].text()
    # Unicode ideographic variation selectors preserve the same base kanji.
    normalized_name = re.sub('[\U000E0100-\U000E01EF]', '', compact(original_name))
    assert normalized_name == '辻元清美'
    assert d.all('house-type')[0].text().startswith('参議院議員') and compact(d.all('district')[0].text()) == '比例'
    cdp = dict(name=normalized_name, original_name=original_name, chamber='参議院', district='全国比例',
        party='立憲民主党', original_location='HTML .member-name span[0]/.house-type[0]/.district[0]',
        name_normalization='空白・NFKCと字形セレクタU+E0100〜U+E01EFのみ除去。漢字・読みの置換なし')
    cdp_sid = source(report, 'cdp-tsujimoto', [cdp], '立憲民主党公式：辻元清美現職プロフィール')
    d, _ = dom('sdp-members')
    text = compact(d.text())
    sdp = []
    for name, reading, term in [('福島みずほ', 'ふくしまみずほ', 5), ('ラサール石井', 'らさーるいしい', 1)]:
        pattern = f'参議院現{term}期・[^選]*{name}{reading}選挙区全国比例区'
        assert re.search(pattern, text)
        sdp.append(dict(name=name, reading=reading, chamber='参議院', district='全国比例', party='社会民主党',
            original_location=f'HTML現職国会議員情報の参議院現{term}期・氏名・選挙区欄（連絡先除外）'))
    sdp_sid = source(report, 'sdp-members', sdp, '社会民主党公式：現職国会議員と全国比例区')
    for sid, records in [(cdp_sid, [cdp]), (sdp_sid, sdp)]:
        for line, r in enumerate(records, 1):
            m = members['senate-' + r['name']]
            observed = [(n, x) for n, x in enumerate(srecords, 1) if x['name'] == r['name']]
            assert len(observed) == 1
            assert (m['name'], m['chamber'], m['district']) == (r['name'], r['chamber'], r['district'])
            report['party_entries'].append(dict(member_id=m['id'], member_name=m['name'], chamber=m['chamber'],
                district=m['district'], party=r['party'], party_as_of=None, checked_at=CHECKED_AT,
                source_id=sid, excerpt_line=line, roster_name=r.get('original_name', r['name']),
                original_location=r['original_location'], parliamentary_roster_evidence=dict(source_id=senate_sid,
                    excerpt_line=observed[0][0], as_of='2026-09-04'),
                method='党公式現職の氏名・院・当選区＋参院原本の氏名・院・全国比例一意一致。会派から党籍を推定しない。'))
    d, _ = dom('ldp-fujii-profile')
    assert compact(descendants(d, 'h1')[0].text()) == '藤井ひさゆき'
    related = [compact(x.text()) for x in descendants(d, 'h3') if '藤井比早之衆議院議員' in compact(x.text())]
    assert len(related) == 1 and '兵庫県第4区' in d.text()
    fujii = dict(name='藤井比早之', profile_heading='藤井ひさゆき', chamber='衆議院', district='兵庫4',
        party='自由民主党', identity_excerpt=related[0],
        original_location='党本人プロフィール h1・選挙区表・本人関連ニュース h3正式名/衆議院議員')
    fsid = source(report, 'ldp-fujii-profile', [fujii], '自民党公式：藤井ひさゆき本人プロフィールと藤井比早之正式名')
    m = members['house-藤井比早之']
    verified = next(x for x in roster['entries'] if x['member_id'] == m['id'])
    assert (m['name'], m['chamber'], m['district']) == ('藤井比早之', '衆議院', '兵庫4')
    assert verified['newverifieddistrict'] == '兵庫4'
    lr = next(x for x in selected_ldp if x['name'] == '藤井ひさゆき')
    assert district_matches(m, lr)
    report['party_entries'].append(dict(member_id=m['id'], member_name=m['name'], chamber=m['chamber'], district=m['district'],
        party='自由民主党', party_as_of=None, checked_at=CHECKED_AT, source_id=fsid, excerpt_line=1,
        roster_name=fujii['profile_heading'], original_location=fujii['original_location'],
        supporting_party_evidence=dict(source_id=ldp_sid, excerpt_line=selected_ldp.index(lr)+1),
        parliamentary_roster_evidence=dict(**verified['evidence'], as_of=verified['newverified_as_of']),
        method='党本人プロフィールに掲載された正式名・院と本人選挙区を国会原本で照合。現職JSONも一致。読みだけによる異名推定はしない。'))
    report['stats'] = dict(additional_party_matches=len(report['party_entries']), service_end_observations=1,
        new_roster_observations_held=1, conflicts_resolved=0)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    generate_districts()
    validate()


def generate_districts():
    report = dict(schema_version=1, checked_at=CHECKED_AT,
        note='総務省の現行区割り入口と公式PDF。郡内町村の個別所属は推定しない。配分決定時点の境界・議員・党籍は別確認。',
        sources=[], originals=[], mappings=[], issues=[], retrieval_attempts=[], stats={})
    d, _ = dom('mic-boundaries')
    linked = [x for x in descendants(d, 'a') if compact(x.text()) == '愛知県']
    assert len(linked) == 1 and linked[0].attrs['href'] == '/main_content/000853828.pdf'
    t = compact(d.text())
    assert '令和4年11月28日' in t and '同年12月28日から施行' in t
    source(report, 'mic-boundaries', [dict(prefecture='愛知県', pdf_url=TARGETS['mic-aichi-boundaries'],
        law_promulgation_date='2022-11-28', effective_from='2022-12-28',
        original_location='HTML本文の区割り改定法施行日・全県区割り表 愛知県リンク')], '総務省：衆院小選挙区改定法と愛知県区域原本')
    original('mic-aichi-boundaries')
    text = subprocess.check_output(['pdftotext', '-layout', str(CACHE / 'mic-aichi-boundaries.bin'), '-']).decode()
    records = []
    for number, areas in re.findall(r'^第([１-９]|1[0-6])区\s+(.+)$', text, re.M):
        records.append(dict(prefecture='愛知県', district='愛知'+str(int(compact(number))),
            area_labels=areas.strip().split('、'), original_location=f'PDF p.1 第{number}区 区域列'))
    assert len(records) == 16 and [r['district'] for r in records] == ['愛知'+str(i) for i in range(1, 17)]
    # A second extraction order verifies all sixteen district/area strings.
    rawtext = subprocess.check_output(['pdftotext', '-raw', str(CACHE / 'mic-aichi-boundaries.bin'), '-']).decode()
    for r in records:
        assert ''.join(r['area_labels']) in compact(rawtext).replace('、', '')
    sid = source(report, 'mic-aichi-boundaries', records, '総務省公式PDF：愛知県16小選挙区の区域')
    by_name = {}
    counties = []
    for line, r in enumerate(records, 1):
        for label in r['area_labels']:
            if label.endswith('郡'):
                counties.append(dict(county=label, district=r['district'], excerpt_line=line)); continue
            assert label.endswith('市') or label.startswith('名古屋市') and label.endswith('区')
            name = '名古屋市' if label.startswith('名古屋市') else label
            by_name.setdefault(name, []).append((line, r, label))
    for name, parts in by_name.items():
        districts = sorted({r['district'] for _, r, _ in parts}, key=lambda d:int(d.removeprefix('愛知')))
        split = len(districts) > 1
        report['mappings'].append(dict(prefecture='愛知県', municipality=name, municipality_code=None,
            chamber='衆議院', election_type='小選挙区', districts=districts,
            coverage='municipality_split' if split else 'municipality_whole',
            as_of=None, effective_from='2022-12-28', source_id=sid,
            boundary_detail=[dict(district=district, area='、'.join(label for _, r, label in parts if r['district']==district)) for district in districts] if split else None,
            evidence=[dict(source_id=sid, excerpt_line=line, original_location=r['original_location'], municipality_label=label) for line, r, label in parts],
            note='総務省現行区域原本。市全体配分を区へ按分しない。配分決定時点の境界と議員は未照合。'))
    assert len(report['mappings']) == 38
    report['issues'].append(dict(status='原本に町村個別非掲載', county_observations=counties,
        reason='区域表は郡名まで。郡内町村所属の独立資料未照合のため各町村へ展開しない。'))
    failure = json.loads((CACHE / 'aichi-boundaries.receipt.json').read_text())
    assert failure['url'] == TARGETS['aichi-boundaries'] and failure['download_status'] == 'blocked_or_failed'
    report['retrieval_attempts'].append(failure)
    report['issues'].append(dict(status='取得不能', source_url=failure['url'],
        retrieval_attempt_source_id=failure['source_id'], http_status=failure['http_status'],
        prior_observation=dict(http_status=403, retrieved_at_utc=None, policy_enforcement_state='unknown',
            note='通信適用状態更新前の観測。正確な取得時刻は未保存のためnull。'),
        reason='通信状態の更新前はHTTP403、更新後の一巡はHTTP302で取得未完了。302の原因は未確定。総務省別原本の取得成功と区別し、県原本との相互照合は未完了。'))
    report['stats'] = dict(municipalities=38, whole_municipalities=37, split_municipalities=1,
        wards=16, district_records=16, unexpanded_counties=len(counties))
    DISTRICTS_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


def validate():
    members = {m['id']:m for m in json.loads((ROOT/'public/data.json').read_text())['legislators']}
    for filename in [REPORT, DISTRICTS_REPORT]:
        report = json.loads(filename.read_text())
        for failure in report['retrieval_attempts']:
            checked(failure['url'])
            assert failure['url'] == TARGETS[failure['source_id']]
            assert failure['download_status'] == 'blocked_or_failed' and failure['http_status'] in {302, 403}
            assert failure['failure_category'] == 'http_error'
            datetime.fromisoformat(failure['retrieved_at_utc'])
        excerpts = {}
        for s in report['sources']:
            assert re.fullmatch(r'roster-continued-[a-z0-9-]+\.txt',s['filename'])
            checked(s['url']); checked(s['original']['final_url'])
            assert s['original'] in report['originals'] and s['original']['url'] == s['url']
            assert s['original']['download_status'] == 'downloaded'
            assert 0 < s['original']['bytes'] <= MAX_BYTES
            assert re.fullmatch('[0-9a-f]{64}',s['original']['sha256_original'])
            text = (ROOT/'data/source-text'/s['filename']).read_bytes()
            assert hashlib.sha256(text).hexdigest() == s['sha256']
            excerpts[s['id']] = [json.loads(line) for line in text.decode().splitlines()]
        if filename == REPORT:
            assert len(report['party_entries']) == 4
            for e in report['party_entries']:
                m=members[e['member_id']]; r=excerpts[e['source_id']][e['excerpt_line']-1]
                assert (m['name'],m['chamber'],m['district']) == (e['member_name'],e['chamber'],e['district'])
                assert (r['name'],r['chamber'],r['district'],r['party']) == (e['member_name'],e['chamber'],e['district'],e['party'])
                assert e['party_as_of'] is None and e['original_location'] == r['original_location']
                proof=e['parliamentary_roster_evidence']
                if proof['source_id'] in excerpts:
                    row=excerpts[proof['source_id']][proof['excerpt_line']-1]
                    assert (row['name'],row['chamber'],row['district'],row['as_of']) == (m['name'],m['chamber'],m['district'],proof['as_of'])
                else:
                    row=next(x for x in json.loads((ROOT/'data/reviewed-roster-followup.json').read_text())['entries'] if x['member_id']==m['id'])
                    assert row['evidence']['source_id']==proof['source_id'] and row['newverifieddistrict']==m['district']
            e=report['member_enrichments'][0];m=members[e['member_id']]
            assert (m['name'],m['as_of'],m['source_url'],m['district']) == (e['member_name'],e['baseline_as_of'],e['baseline_source_url'],e['baseline_district'])
            assert e['service_end_date']=='2026-09-17' and e['current_roster_eligible'] is False
            assert len(report['held_records'])==1
            held=report['held_records'][0]
            assert held['proposed_member_id'] is None
            proof=held['profile_evidence'];r=excerpts[proof['source_id']][proof['excerpt_line']-1]
            assert (held['name'],held['chamber'],held['district'],held['as_of'])==(r['name'],r['chamber'],r['district'],r['as_of'])
            party=held['party_observation'];r=excerpts[party['source_id']][party['excerpt_line']-1]
            assert r['name']==held['name'] and r['chamber']==held['chamber'] and party['party']==r['party'] and party['as_of'] is None
            assert district_matches(dict(held,election_type='小選挙区'),r)
            prior=json.loads((ROOT/'data/reviewed-roster-followup.json').read_text())
            proof=held['roster_evidence'];s=next(x for x in prior['sources'] if x['id']==proof['source_id'])
            raw=(ROOT/'data/source-text'/s['filename']).read_bytes()
            assert hashlib.sha256(raw).hexdigest()==s['sha256']
            r=json.loads(raw.decode().splitlines()[proof['excerpt_line']-1])
            assert (r['name'],r['chamber'],r['district'],r['as_of'])==(held['name'],held['chamber'],held['district'],proof['as_of'])
        else:
            seen=set()
            for m in report['mappings']:
                assert m['municipality'] not in seen;seen.add(m['municipality'])
                assert m['municipality_code'] is None and m['as_of'] is None and m['effective_from']=='2022-12-28'
                ds=set()
                for e in m['evidence']:
                    r=excerpts[e['source_id']][e['excerpt_line']-1]
                    assert e['municipality_label'] in r['area_labels'] and e['original_location']==r['original_location']
                    assert e['municipality_label']==m['municipality'] or m['municipality']=='名古屋市' and e['municipality_label'].startswith('名古屋市')
                    ds.add(r['district'])
                assert ds==set(m['districts'])
                assert len(ds)>1 if m['coverage']=='municipality_split' else len(ds)==1
            assert len(seen)==38 and report['stats']['whole_municipalities']==37
        print(json.dumps(dict(report=filename.name, stats=report['stats']),ensure_ascii=False))


def register_sources(report, root, source):
    """Register checked excerpts; normal builds never need original binaries."""
    for s in report['sources']:
        checked(s['url'])
        assert re.fullmatch(r'roster-continued-[a-z0-9-]+\.txt',s['filename'])
        raw=(root/s['filename']).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==s['sha256']
        assert s['original']['url']==s['url'] and s['original'] in report['originals']
        kind='所属党' if s['id'] in {'roster-continued-cdp-tsujimoto','roster-continued-sdp-members','roster-continued-ldp-fujii-profile','roster-continued-ldp-json'} else '議員名簿・地域対応'
        source(s['id'],s['filename'],s['title'],s['url'],s['method'],raw.decode(),s['as_of'],kind=kind)


def apply_roster_continued(members, root, source):
    """Preserve baseline identities, append proofs, and mark the confirmed death.

    The caller handles current-member filtering and displays held_records as
    separate observations; this function never creates a replacement identity.
    """
    report=json.loads((root.parent/'reviewed-roster-continued.json').read_text())
    register_sources(report,root,source)
    people={m['id']:m for m in members}; sources={s['id']:s for s in report['sources']}
    for e in report['member_enrichments']:
        if e['member_id'] not in people:
            continue
        m=people[e['member_id']]
        assert (m['name'],m['chamber'],m['as_of'],m['source_url'],m['district']) == (e['member_name'],e['chamber'],e['baseline_as_of'],e['baseline_source_url'],e['baseline_district'])
        proof=e['evidence'];s=sources[proof['source_id']]
        r=json.loads((root/s['filename']).read_text().splitlines()[proof['excerpt_line']-1])
        assert (r['name'],r['chamber'],r['event'],r['event_date'])==(m['name'],m['chamber'],'逝去',e['service_end_date'])
        m['service_status']=e['service_status']
        m['current_roster_eligible']=e['current_roster_eligible']
        m['service_end_date']=e['service_end_date']
        m.setdefault('service_evidence',[]).append(dict(**proof,event='逝去',event_date=e['service_end_date'],checked_at=report['checked_at'],note=e['note']))
    for e in report['party_entries']:
        if e['member_id'] not in people:
            continue
        m=people[e['member_id']];s=sources[e['source_id']]
        r=json.loads((root/s['filename']).read_text().splitlines()[e['excerpt_line']-1])
        assert (m['name'],m['chamber'],m['district'])==(e['member_name'],e['chamber'],e['district'])
        assert (r['name'],r['chamber'],r['district'],r['party'])==(m['name'],m['chamber'],m['district'],e['party'])
        assert e['party_as_of'] is None
        m['party_evidence'].append(dict(party=e['party'],url=s['url'],as_of=None,checked_at=e['checked_at'],
            source_id=e['source_id'],method=e['method'],roster_name=e['roster_name'],roster_district=e['district'],
            excerpt_line=e['excerpt_line'],original_location=e['original_location'],
            parliamentary_roster_evidence=e['parliamentary_roster_evidence'],
            supporting_party_evidence=e.get('supporting_party_evidence')))
    return report


def apply_aichi_districts(root, source):
    report=json.loads((root.parent/'reviewed-aichi-districts.json').read_text())
    register_sources(report,root,source)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--generate', action='store_true')
    parser.add_argument('--keys', nargs='*', choices=sorted(TARGETS), default=list(TARGETS))
    args = parser.parse_args()
    if args.fetch:
        fetch(args.keys)
    if args.generate:
        generate()
    elif not args.fetch:
        validate()
