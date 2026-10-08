"""Review two exact party identities and keep remaining checks on hold.

Current membership statements, dated election nominations, parliamentary
caucuses and different name spellings remain distinct observations. Baseline
roster identity and dates are never replaced. Network access is opt-in.
"""
import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler
from fetch_sources import failure_details
from verify_party_expansion import DOM, Node, compact
from verify_roster_followup import descendants

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/party-fourth-stage'
REPORT = ROOT / 'data/reviewed-party-fourth-stage.json'
CHECKED_AT = '2026-10-09'
TARGETS = {
    'genzei-home': 'https://genzeinippon.com/',
    'genzei-message': 'https://genzeinippon.com/about/message/',
    'genzei-kawamura': 'https://genzeinippon.com/member/kawamura',
    'house-kawamura': 'https://www.shugiin.go.jp/Internet/itdb_giinprof.nsf/html/profile/135.html',
    'ldp-sakurai': 'https://www.jimin.jp/member/203489.html',
    'senate-sakurai': 'https://www.sangiin.go.jp/japanese/joho1/kousei/giin/profile/5998030.htm',
    'komei-miyazaki': 'https://www.komei.or.jp/member/detail/11025120',
    'senate-miyazaki': 'https://www.sangiin.go.jp/japanese/joho1/kousei/giin/profile/7016037.htm',
    'ldp-isozaki': 'https://www.jimin.jp/member/100557.html',
    'senate-isozaki': 'https://www.sangiin.go.jp/japanese/joho1/kousei/giin/profile/7010008.htm',
    **{f'ldp-cand-{key}': f'https://www.jimin.jp/election/results/sen_shu51/candidate/detail/{slug}.html'
       for key, slug in [('uchiyama', 'uchiyama-ko'), ('nakagawa', 'nakagawa-koichi'),
                         ('korai', 'korai-keiichiro'), ('maruo', 'maruo-natsuko')]},
}
HOSTS = {urlsplit(u).hostname for u in TARGETS.values()}
MAX_BYTES = 4_000_000


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
    assert policy['type'] == 'unrestricted' or {urlsplit(TARGETS[k]).hostname for k in keys} <= {r['host'] for r in policy['egress_rules']}
    CACHE.mkdir(parents=True, exist_ok=True)
    for key in keys:
        url = checked(TARGETS[key]); at = datetime.now(timezone.utc).isoformat()
        try:
            with build_opener(Redirect()).open(url, timeout=25) as response:
                raw = response.read(MAX_BYTES + 1); assert 0 < len(raw) <= MAX_BYTES
                receipt = dict(source_id=key, url=url, final_url=checked(response.geturl()), retrieved_at_utc=at,
                    download_status='downloaded', bytes=len(raw), sha256_original=hashlib.sha256(raw).hexdigest(), content_type=response.headers.get_content_type())
            (CACHE / (key + '.bin')).write_bytes(raw)
        except Exception as error:
            receipt = dict(source_id=key, url=url, retrieved_at_utc=at, download_status='blocked_or_failed', **failure_details(error))
        (CACHE / (key + '.receipt.json')).write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(dict(source_id=key, download_status=receipt['download_status'])))


def original(key):
    receipt = json.loads((CACHE / (key + '.receipt.json')).read_text())
    assert receipt['url'] == TARGETS[key] and receipt['download_status'] == 'downloaded'
    raw = (CACHE / (key + '.bin')).read_bytes()
    assert 0 < len(raw) == receipt['bytes'] <= MAX_BYTES and hashlib.sha256(raw).hexdigest() == receipt['sha256_original']
    checked(receipt['final_url'])
    d = DOM(); d.feed(raw.decode('cp932' if key == 'house-kawamura' else 'utf-8'))
    return d.root, receipt


def ruby_name(d):
    return compact(''.join(x for n in descendants(d, 'ruby') for x in n.children if isinstance(x, str)))


def save_source(report, key, records, title):
    _, receipt = original(key)
    sid = 'party-fourth-' + key; filename = sid + '.txt'
    text = '\n'.join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in records) + '\n'
    (ROOT / 'data/source-text' / filename).write_text(text)
    report['sources'].append(dict(id=sid, url=TARGETS[key], filename=filename, title=title, as_of=None,
        checked_at=CHECKED_AT, sha256=hashlib.sha256(text.encode()).hexdigest(), original=receipt,
        kind='議員名簿' if key.startswith(('house-', 'senate-')) else '所属党',
        method='氏名・院・当選した選挙区を独立原本で確認。会派・選挙時公認・資料日不明を現在党籍に読み替えない。'))
    report['originals'].append(receipt)
    return sid


def parliament_record(key, name, chamber, district):
    d, _ = original(key)
    heading = descendants(d, 'h2')[0] if key.startswith('house-') else d.all('profile-name')[0]
    assert compact(heading.text()).startswith(name+'(')
    text = compact(d.text())
    if key == 'house-kawamura':
        assert '小選挙区(愛知県第一区)選出' in text and '(令和8年4月現在)' in text
        record_dates = dict(biography_as_of_month='2026-04', precise_biography_date=None)
    else:
        target = '比例代表選出' if district == '全国比例' else '選挙区('+district+')選出'
        assert target in text
        role = re.search(r'令和8年(\d+)月(\d+)日現在',text)
        record_dates = dict(role_as_of=f'2026-{int(role[1]):02d}-{int(role[2]):02d}' if role else None,
            biography_as_of='2025-12-18' if '(令和7年12月18日現在)' in text else None)
    return dict(name=name, chamber=chamber, district=district, date_fields=record_dates,
        original_location='HTMLの氏名見出し・当選した選挙区欄。会派を党籍に使わず、役職日・略歴日を文書全体の資料日へ転用しない。')


def generate():
    members = json.loads((ROOT/'public/data.json').read_text())['legislators']
    byid = {m['id']:m for m in members}
    report = dict(schema_version=1, checked_at=CHECKED_AT,
        note='現在党籍の新規照合は2人のみ。旧711人の氏名・ID・院・当選区・資料日・URL・党籍証跡を保持。現在の所属党確認と予算配分決定時点の確認は別。異体字や読みだけでは同一人物と確定しない。',
        sources=[], originals=[], entries=[], historical_nominations=[], held_records=[], issues=[], stats={})
    d,_=original('genzei-kawamura')
    assert ruby_name(d)=='河村たかし' and compact(d.all('position')[0].text())=='衆議院議員/愛知1区'
    assert '主な役職代表' in compact(d.text()) and '河村たかしが代表を務める政党-減税日本' in compact(d.text())
    r=dict(name='河村たかし', chamber='衆議院', district='愛知1', party='減税日本', role='代表',
        original_location='HTML .name ruby（rt除外）/.position/主な役職/党代表表示')
    sid=save_source(report,'genzei-kawamura',[r],'減税日本公式：河村たかし現職議員と代表')
    supports=[]
    for key in ['genzei-home','genzei-message']:
        d,_=original(key); t=compact(d.text())
        if key=='genzei-home':
            cards=[a for a in descendants(d,'a') if a.attrs.get('href')==TARGETS['genzei-kawamura']]
            assert len(cards)==1 and '衆議院議員/愛知1区河村たかし' in compact(cards[0].text())
            detail=dict(name='河村たかし', chamber='衆議院', district='愛知1', party='減税日本', role='代表',
                profile_url=TARGETS['genzei-kawamura'], original_location='党公式トップの現職メンバーカード・本人プロフィールリンク')
        else:
            assert '減税日本代表/衆議院議員/前名古屋市長' in t and '衆議院議員の河村たかしでございます' in t
            detail=dict(name='河村たかし', chamber='衆議院', party='減税日本', role='代表',
                original_location='HTML .positionと代表本人あいさつ冒頭（連絡先除外）')
        supports.append(dict(source_id=save_source(report,key,[detail],'減税日本公式：現職メンバー・代表の独立確認'),excerpt_line=1))
    pr=parliament_record('house-kawamura','河村たかし','衆議院','愛知1')
    psid=save_source(report,'house-kawamura',[pr],'衆議院公式：河村たかし氏名と愛知1区')
    m=byid['house-河村たかし'];assert (m['name'],m['chamber'],m['district'])==('河村たかし','衆議院','愛知1')
    report['entries'].append(dict(member_id=m['id'],member_name=m['name'],chamber=m['chamber'],district=m['district'],party='減税日本',
        party_as_of=None,checked_at=CHECKED_AT,source_id=sid,excerpt_line=1,roster_name=m['name'],original_location=r['original_location'],
        supporting_party_evidence=supports,parliamentary_roster_evidence=dict(source_id=psid,excerpt_line=1,date_fields=pr['date_fields']),
        party_scope='地域政党・政治団体', scope_note='減税日本の代表所属を確認。国政政党の所属、減税日本・ゆうこく連合との関係、配分時点の党籍は別途未照合。',
        method='地域政党・政治団体の減税日本公式現職メンバーと代表本人あいさつの正式名・院・愛知1区＋独立した衆議院本人原本の一意一致。国政政党所属とは別確認。'))
    d,_=original('ldp-sakurai')
    assert compact(descendants(d,'h1')[0].text())=='桜井充'
    labels=[compact(n.text()) for n in descendants(d,'h3') if '櫻井充参議院議員' in compact(n.text())]
    assert len(labels)==1
    areas=[compact(x.text()) for x in descendants(d,'tr') if '選挙区' in x.text()]
    assert '選挙区宮城県' in areas
    r=dict(name='櫻井充',profile_heading='桜井充',chamber='参議院',district='宮城県',party='自由民主党',identity_excerpt=labels[0],
        original_location='党本人プロフィール h1・選挙区表・本人関連記事 h3正式名/参議院議員')
    sid=save_source(report,'ldp-sakurai',[r],'自民党公式：櫻井充正式名と桜井充本人プロフィール')
    pr=parliament_record('senate-sakurai','櫻井充','参議院','宮城県')
    psid=save_source(report,'senate-sakurai',[pr],'参議院公式：櫻井充正式名と宮城選挙区')
    m=byid['senate-櫻井充'];assert (m['name'],m['chamber'],m['district'])==('櫻井充','参議院','宮城県')
    report['entries'].append(dict(member_id=m['id'],member_name=m['name'],chamber=m['chamber'],district=m['district'],party='自由民主党',
        party_as_of=None,checked_at=CHECKED_AT,source_id=sid,excerpt_line=1,roster_name=r['profile_heading'],original_location=r['original_location'],
        supporting_party_evidence=[],parliamentary_roster_evidence=dict(source_id=psid,excerpt_line=1,date_fields=pr['date_fields']),
        method='党本人プロフィールに掲載された櫻井充参議院議員の正式名・宮城県を独立した参議院本人原本で照合。読みだけの異体字推定をしない。'))
    for key,name,district in [('uchiyama','内山こう','新潟県第1区'),('nakagawa','中川こういち','北海道第11区'),('korai','こうらい啓一郎','大阪府第8区'),('maruo','丸尾なつ子','神奈川県第1区')]:
        d,_=original('ldp-cand-'+key)
        assert ruby_name(d)==name and compact(descendants(d,'h2')[0].text())==district
        assert '公認候補者' in d.text()
        r=dict(name=name,chamber='衆議院',party='自由民主党',status_label='公認候補者',election_frame='2026年第51回衆議院選挙',
            candidacy_district=district,party_membership_as_of=None,current_membership_confirmed=False,
            original_location='選挙特設原本 .heading1-hero/選挙区 h2/氏名 ruby（rt除外）')
        sid=save_source(report,'ldp-cand-'+key,[r],'自民党公式：2026衆院選の公認候補者（現在党籍と別）')
        report['historical_nominations'].append(dict(member_id='house-'+name,source_id=sid,excerpt_line=1,**r,
            note='選挙時点の公認の観測。原本の掲載日・現在党籍の基準日は未確認。立候補予定区を当選区へ使わず、現在党籍へ昇格しない。'))
    for partykey,parliamentkey,name,party,heading,district in [('komei-miyazaki','senate-miyazaki','宮崎勝','公明党','宮﨑勝','全国比例'),('ldp-isozaki','senate-isozaki','磯崎仁彦','自由民主党','いそざき仁彦','香川県')]:
        d,_=original(partykey)
        h=d.all('hdg1_01')[0] if partykey=='komei-miyazaki' else descendants(d,'h1')[0]
        assert compact(h.text()).startswith(heading)
        r=dict(name=heading,chamber='参議院',party=party,
            district='全国比例' if partykey=='komei-miyazaki' else '香川県',
            original_location='党公式現職プロフィール 氏名見出し・参議院議員・選挙区欄')
        text=compact(d.text());assert '参議院議員' in text
        assert '比例区/-' in text if partykey=='komei-miyazaki' else '選挙区香川県' in text
        sid=save_source(report,partykey,[r],'党公式：正式氏名対応保留の現職プロフィール')
        pr=parliament_record(parliamentkey,name,'参議院',district)
        psid=save_source(report,parliamentkey,[pr],'参議院公式：正式氏名・当選区を独立確認（党籍保留）')
        report['held_records'].append(dict(member_id='senate-'+name,member_name=name,possible_party=party,status='氏名対応保留',
            source_id=sid,excerpt_line=1,parliamentary_roster_evidence=dict(source_id=psid,excerpt_line=1),
            reason='党の氏名表記と国会正式名が完全一致せず、今回の原本に両表記を直接結ぶ証跡を確認できない。院・区・読み一致だけでは党籍を確定しない。'))
    resolved={e['member_id'] for e in report['entries']}
    held={e['member_id']:e for e in report['held_records']}
    nomination={e['member_id']:e for e in report['historical_nominations']}
    # Use the immutable baseline verification set, also after a parent rebuild.
    previous=json.loads(REPORT.read_text()) if REPORT.exists() else None
    reviewed_ids=previous['reviewed_baseline_ids'] if previous else [m['id'] for m in members if m['party_status']=='未照合']
    report['reviewed_baseline_ids']=reviewed_ids
    for mid in reviewed_ids:
        if mid in resolved:continue
        m=byid[mid]
        reason=held[mid]['reason'] if mid in held else (
            '旧名簿に保持した逝去議員。現在党籍の確認対象から除外し、予算配分時点の党籍は別途未照合。' if m.get('current_roster_eligible') is False else
            '2026選挙の党公認原本は取得済みだが、現在の所属党資料と表示名の直接対応・党籍基準日が未確認。' if mid in nomination else
            'この限定調査では、院・当選区・正式名が一意一致する現在の党名明示一次資料を確認できていない。会派や党公認・推薦だけで所属党を補完しない。')
        report['issues'].append(dict(member_id=mid,member_name=m['name'],chamber=m['chamber'],district=m['district'],
            party_candidate=held[mid]['possible_party'] if mid in held else nomination[mid]['party'] if mid in nomination else None,
            status='対象外・旧名簿保持' if m.get('current_roster_eligible') is False else '現在党籍未照合',reason=reason))
    report['stats']=dict(baseline_unverified=len(reviewed_ids),additional_current_party_matches=len(report['entries']),
        remaining_unverified=len(report['issues']),focused_original_cases=8,historical_nomination_observations=len(report['historical_nominations']),
        held_spelling_records=len(report['held_records']),deceased_retained=1,conflicts_resolved=0)
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    validate()


def validate():
    report=json.loads(REPORT.read_text());members={m['id']:m for m in json.loads((ROOT/'public/data.json').read_text())['legislators']}
    excerpts={};sources={s['id']:s for s in report['sources']}
    assert len(sources)==len(report['sources'])==14
    for sid,s in sources.items():
        checked(s['url']);assert s['url'] in TARGETS.values()
        assert re.fullmatch(r'party-fourth-[a-z0-9-]+\.txt',s['filename'])
        raw=(ROOT/'data/source-text'/s['filename']).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==s['sha256']
        assert s['original'] in report['originals'] and s['original']['url']==s['url']
        assert s['original']['download_status']=='downloaded' and 0<s['original']['bytes']<=MAX_BYTES
        assert re.fullmatch('[0-9a-f]{64}',s['original']['sha256_original'])
        checked(s['original']['final_url']);datetime.fromisoformat(s['original']['retrieved_at_utc'])
        assert s['as_of'] is None
        excerpts[sid]=[json.loads(line) for line in raw.decode().splitlines()]
    for e in report['entries']:
        m=members[e['member_id']];r=excerpts[e['source_id']][e['excerpt_line']-1]
        assert (m['name'],m['chamber'],m['district'])==(e['member_name'],e['chamber'],e['district'])
        assert (r['name'],r['chamber'],r['district'],r['party'])==(m['name'],m['chamber'],m['district'],e['party'])
        assert e['party_as_of'] is None and e['original_location']==r['original_location']
        proof=e['parliamentary_roster_evidence'];pr=excerpts[proof['source_id']][proof['excerpt_line']-1]
        assert (pr['name'],pr['chamber'],pr['district'],pr['date_fields'])==(m['name'],m['chamber'],m['district'],proof['date_fields'])
        for proof in e['supporting_party_evidence']:
            r=excerpts[proof['source_id']][proof['excerpt_line']-1]
            assert r['name']==m['name'] and r['chamber']==m['chamber'] and r['party']==e['party']
    for e in report['historical_nominations']:
        r=excerpts[e['source_id']][e['excerpt_line']-1]
        assert e['current_membership_confirmed'] is False and e['party_membership_as_of'] is None
        assert e['status_label']==r['status_label']=='公認候補者' and e['name']==members[e['member_id']]['name']
        assert r['candidacy_district']==e['candidacy_district'] and r['current_membership_confirmed'] is False
    for e in report['held_records']:
        m=members[e['member_id']];r=excerpts[e['source_id']][e['excerpt_line']-1]
        assert r['name']!=m['name'] and r['chamber']==m['chamber'] and r['district']==m['district'] and r['party']==e['possible_party']
    resolved={e['member_id'] for e in report['entries']};unresolved={e['member_id'] for e in report['issues']}
    assert not resolved & unresolved and resolved | unresolved == set(report['reviewed_baseline_ids'])
    assert len(resolved)==2 and len(unresolved)==22
    print(json.dumps(report['stats'],ensure_ascii=False))
    return report


def apply_party_fourth_stage(members, root, source):
    report=json.loads((root.parent/'reviewed-party-fourth-stage.json').read_text())
    sources={s['id']:s for s in report['sources']}
    for s in sources.values():
        checked(s['url']);assert s['url'] in TARGETS.values()
        assert re.fullmatch(r'party-fourth-[a-z0-9-]+\.txt',s['filename'])
        raw=(root/s['filename']).read_bytes();assert hashlib.sha256(raw).hexdigest()==s['sha256']
        source(s['id'],s['filename'],s['title'],s['url'],s['method'],raw.decode(),None,kind=s['kind'])
    people={m['id']:m for m in members}
    for e in report['entries']:
        if e['member_id'] not in people:
            continue  # Unit fixtures may contain a subset of the saved roster.
        m=people[e['member_id']];s=sources[e['source_id']]
        assert (m['name'],m['chamber'],m['district'])==(e['member_name'],e['chamber'],e['district'])
        r=json.loads((root/s['filename']).read_text().splitlines()[e['excerpt_line']-1])
        assert (r['name'],r['chamber'],r['district'],r['party'])==(m['name'],m['chamber'],m['district'],e['party'])
        assert e['party_as_of'] is None
        m['party_evidence'].append(dict(party=e['party'],url=s['url'],as_of=None,checked_at=e['checked_at'],source_id=e['source_id'],
            method=e['method'],roster_name=e['roster_name'],roster_district=e['district'],excerpt_line=e['excerpt_line'],
            original_location=e['original_location'],parliamentary_roster_evidence=e['parliamentary_roster_evidence'],
            supporting_party_evidence=e['supporting_party_evidence'],party_scope=e.get('party_scope'),scope_note=e.get('scope_note')))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--fetch',action='store_true');parser.add_argument('--generate',action='store_true')
    parser.add_argument('--keys',nargs='*',choices=sorted(TARGETS),default=list(TARGETS));args=parser.parse_args()
    if args.fetch:fetch(args.keys)
    if args.generate:generate()
    elif not args.fetch:validate()
