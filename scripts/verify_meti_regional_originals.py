"""Recheck authorized energy-grant originals; failed retrieval is not nonpublication."""
import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from fetch_sources import CheckedRedirect, MAX_BYTES, checked, failure_details

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/originals'
OUTPUT = ROOT / 'data/reviewed-meti-regional.json'
URLS = [
    'https://www.enecho.meti.go.jp/committee/disclosure/dengenkoufukin1/',
    'https://www.enecho.meti.go.jp/committee/disclosure/dengenkoufukin1/index.html',
    'https://www.enecho.meti.go.jp/committee/disclosure/dengenkoufukin2/',
    'https://www.enecho.meti.go.jp/category/electricity_and_gas/rittishien/',
    'https://www.enecho.meti.go.jp/',
    'https://www.meti.go.jp/main/yosan/yosan_fy2026/pdf/01.pdf',
]


def fetch():
    CACHE.mkdir(parents=True, exist_ok=True)
    opener = build_opener(CheckedRedirect())
    for url in URLS:
        receipt = dict(url=url, retrieved_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
        try:
            with opener.open(Request(checked(url), headers={
                    'User-Agent': 'japan-budget-tracker/0.1 (public official budget research)'}), timeout=25) as response:
                size = response.headers.get('Content-Length')
                if size and int(size) > MAX_BYTES:
                    raise ValueError('File exceeds size limit')
                content = response.read(MAX_BYTES + 1)
                if len(content) > MAX_BYTES:
                    raise ValueError('File exceeds size limit')
                receipt.update(status='downloaded', final_url=checked(response.geturl()),
                               http_status=response.status, bytes=len(content),
                               sha256_original=hashlib.sha256(content).hexdigest(),
                               content_type=response.headers.get_content_type())
                (CACHE / (hashlib.sha256(url.encode()).hexdigest() + '.bin')).write_bytes(content)
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            receipt.update(status='blocked_or_failed', error_type=type(error).__name__, **failure_details(error))
        with (CACHE / 'manifest.jsonl').open('a') as stream:
            stream.write(json.dumps(receipt, ensure_ascii=False) + '\n')
        print(json.dumps({k: receipt[k] for k in ('url', 'status', 'http_status', 'failure_category') if k in receipt}))


def update(policy_state):
    report = json.loads(OUTPUT.read_text())
    previous = report['retrieval_attempts']
    attempts = [json.loads(line) for line in (CACHE / 'manifest.jsonl').read_text().splitlines()]
    latest = {r['url']: r for r in attempts if r['url'] in URLS}
    for url in URLS:
        if url not in latest:
            continue
        receipt = {k: v for k, v in latest[url].items() if k in (
            'url', 'retrieved_at_utc', 'status', 'http_status', 'failure_category', 'error_type',
            'final_url', 'bytes', 'sha256_original', 'content_type')}
        if receipt not in previous:
            previous.append(receipt)
        if receipt['status'] == 'downloaded':
            content = (CACHE / (hashlib.sha256(url.encode()).hexdigest() + '.bin')).read_bytes()
            assert len(content) == receipt['bytes'] and hashlib.sha256(content).hexdigest() == receipt['sha256_original']
            if not any(o['url'] == url for o in report['originals']):
                original = {k: receipt[k] for k in ('url', 'final_url', 'retrieved_at_utc', 'sha256_original', 'bytes', 'content_type')}
                original.update(source_ids=[], verification=dict(status='downloaded_only', checked_at='2026-10-09',
                                method='原本取得・ハッシュ確認のみ。掲載年度・金額・比較条件は未照合。', locator='未照合', row_ids=[], fields=[]))
                report['originals'].append(original)
    failed = all(latest.get(url, {}).get('status') == 'blocked_or_failed' for url in URLS)
    old_observation = report.get('network_observation')
    history = report.setdefault('network_observations', [])
    if old_observation and old_observation not in history:
        history.append(old_observation)
    report['network_observation'] = dict(
        checked_at='2026-10-09', runtime_policy_contains_enecho=any(
            r['host'] == 'www.enecho.meti.go.jp' for r in json.loads(
                Path('/etc/codex/network-policy.json').read_text())['http_network_policy']['egress_rules']),
        environment_policy_state=policy_state, proxy_and_tls_preserved=True,
        note='環境ツールで確認した適用状態と取得実測を分離。HTTPErrorの403は前回URLErrorのCONNECT 403と別の失敗種別で、発生主体は未確定。過去のunknown観測は別履歴で保持。')
    if report['network_observation'] not in history:
        history.append(report['network_observation'])
    report['research_notes'] = [dict(
        ministry='経済産業省', agency='資源エネルギー庁',
        status=f'通信許可{policy_state}確認後もHTTP 403・原本未取得' if failed else '取得結果更新・地域配分の数値照合は未完了',
        source_ids=[], requested_url=URLS[0],
        note='電源立地地域対策交付金の事業概要・評価報告・制度入口を追加許可後に再確認。今回はHTTP応答403で原本取得できず、前回のプロキシCONNECT拒否とは分離。検索抽出は令和4〜6年度掲載を示すが、2025/2026年度の非掲載を原本で確認したとは扱わない。経産省2026年度予算概要の候補も403。全国予算と地域交付、当初と実施後評価を混ぜず、地域配分・同期間執行は取得不能／未確認として残す。',
        checked_at='2026-10-09', retrieval_attempts=previous,
        next_steps=['HTTP 403の発生主体は未確定。通信許可は適用済みと確認したが取得できず、プロキシ解除・TLS無効化・別経路での原本取得は行わない。' if policy_state == 'enforced' else '環境ポリシー適用状態とHTTP 403の発生主体を確認する。プロキシ解除・TLS無効化・別経路での原本取得は行わない。',
                    '原本到達後、2025/2026の交付決定・実績・評価年度を分けて確認し、同じ財源・対象・段階・期間だけを比較する。'])]
    report['unadopted_discoveries'] = [dict(
        url=url, evidence_status='Exa検索抽出のみ・公式原本未取得',
        finding='令和4〜6年度実施事業の掲載とする検索抽出。2025/2026年度地域原本の掲載状態は未確認。')
        for url in URLS if 'dengenkoufukin' in url and 'index.html' not in url]
    report['search_provenance'] = dict(provider='Exa', sources_reviewed_requested_results=10,
                                       search_text_is_not_original=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


def verify():
    report = json.loads(OUTPUT.read_text())
    assert report['rows'] == [] and report['sources'] == []
    assert report['network_observation']['proxy_and_tls_preserved']
    for attempt in report['retrieval_attempts']:
        checked(attempt['url'])
        assert attempt['status'] in ('downloaded', 'blocked_or_failed')
        if attempt['status'] == 'blocked_or_failed':
            assert 'sha256_original' not in attempt
    assert report['research_notes'][0]['retrieval_attempts'] == report['retrieval_attempts']
    print(json.dumps(dict(attempts=len(report['retrieval_attempts']), comparison_rows=0,
                          status=report['research_notes'][0]['status']), ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--policy-state', choices=('unknown', 'enforced'), default='unknown',
                        help='環境ツールで実際に確認した適用状態。推測で指定しない。')
    args = parser.parse_args()
    if args.fetch:
        fetch()
    if args.write:
        update(args.policy_state)
    verify()


if __name__ == '__main__':
    main()
