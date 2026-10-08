"""Bounded recheck of official entrypoints after a change of execution environment.

No automatic retries, alternate fetch paths or proxy/TLS changes. --fetch records
one attempt per fixed URL; --write reviews the current policy-page link. Default
validation reads committed evidence only and is suitable for offline builds.
"""
import argparse
import hashlib
import json
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from fetch_sources import CheckedRedirect, MAX_BYTES, checked, failure_details

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/retrieval-followup'
OUTPUT = ROOT / 'data/reviewed-retrieval-followup.json'
POLICY = 'https://www.cao.go.jp/seisaku/seisaku.html'
REGIONAL = 'https://www.chisou.go.jp/sousei/index.html'
URLS = (
    'https://www.enecho.meti.go.jp/committee/disclosure/dengenkoufukin1/',
    'https://www.enecho.meti.go.jp/',
    'https://www.chisou.go.jp/sousei/about/chiikimiraikoufukin/suishin/pdf/r8_suishin_1.pdf',
    POLICY,
    'https://www.meti.go.jp/main/yosan/yosan_fy2026/index.html',
    REGIONAL,
    'https://www.chisou.go.jp/sousei/about/shinchihoukouhukin/pdf/saitaku_hosei2_tosyo_r7.pdf',
)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.href = None
        self.parts = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.href = dict(attrs).get('href')
            self.parts = []

    def handle_data(self, value):
        if self.href is not None:
            self.parts.append(value)

    def handle_endtag(self, tag):
        if tag == 'a' and self.href is not None:
            self.links.append((''.join(self.parts).strip(), self.href))
            self.href = None


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
                receipt.update(status='downloaded', http_status=response.status,
                    final_url=checked(response.geturl()), bytes=len(content),
                    sha256_original=hashlib.sha256(content).hexdigest(),
                    content_type=response.headers.get_content_type())
                # Content-addressed variants preserve earlier reviewed bytes.
                (CACHE / (hashlib.sha256(url.encode()).hexdigest() + '.' + receipt['sha256_original'] + '.bin')).write_bytes(content)
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            receipt.update(status='blocked_or_failed', error_type=type(error).__name__, **failure_details(error))
        with (CACHE / 'manifest.jsonl').open('a') as stream:
            stream.write(json.dumps(receipt, ensure_ascii=False) + '\n')
        print(json.dumps({k: receipt[k] for k in ('url', 'status', 'http_status', 'failure_category') if k in receipt}))


def generate(observation_file):
    supplied = json.loads(observation_file.read_text())
    keys = ('observed_at_utc', 'spec_revision', 'observed_spec_revision', 'observations_current',
            'network_policy_mode', 'network_policy_state', 'runtime_http_policy_type')
    observation = {k: supplied[k] for k in keys}
    assert observation['observations_current'] is True
    assert observation['network_policy_state'] in ('unknown', 'enforced')
    observation['proxy_and_tls_preserved'] = True
    attempts = [json.loads(line) for line in (CACHE / 'manifest.jsonl').read_text().splitlines()]
    latest = {r['url']: r for r in attempts if r['url'] in URLS}
    assert set(latest) == set(URLS)
    attempts = list(latest.values())
    for receipt in attempts:
        receipt['network_observation'] = observation
    receipt = latest[POLICY]
    assert receipt['status'] == 'downloaded'
    key = hashlib.sha256(POLICY.encode()).hexdigest()
    path = CACHE / (key + '.' + receipt['sha256_original'] + '.bin')
    if not path.exists():
        path = CACHE / (key + '.bin')
    content = path.read_bytes()
    assert len(content) == receipt['bytes'] and hashlib.sha256(content).hexdigest() == receipt['sha256_original']
    parser = Links()
    parser.feed(content.decode('utf-8'))
    matches = [label for label, url in parser.links if url == REGIONAL]
    assert matches == ['地方創生（内閣官房・内閣府 総合サイト）']
    sid = 'retrieval-followup-cao-policy'
    excerpt = matches[0] + '\n' + REGIONAL
    original = {k: receipt[k] for k in ('url', 'final_url', 'retrieved_at_utc', 'sha256_original', 'bytes', 'content_type')}
    original.update(source_ids=[sid], verification=dict(status='downloaded_only', checked_at='2026-10-09',
        method='HTMLParserで現行政策ページの案内名とhrefを抽出。採択原本・予算金額の照合ではない。',
        locator='地方創生のリンク', row_ids=[], fields=[]))
    source = dict(id=sid, title='内閣府の政策：地方創生の現行案内（今回再確認）', url=POLICY,
        locator='地方創生（内閣官房・内閣府 総合サイト）へのリンク', kind='予算・議員資料',
        accessed='2026-10-09', published=None, retrieved_via='公式HTML原本を既存プロキシ・TLS検証で取得',
        excerpt=excerpt, sha256_extracted_text=hashlib.sha256(excerpt.encode()).hexdigest())
    common = dict(checked_at='2026-10-09', network_observation=observation)
    notes = [dict(ministry='経済産業省', agency='資源エネルギー庁',
        status='今回の変更環境でもHTTP 403・原本未取得', source_ids=[], requested_url=URLS[0],
        note='仕様3・unrestricted/unknownの環境で事業概要とトップを各1回確認しHTTP応答403。経産省2026予算入口も403。前回仕様5・enforcedの試行と別観測。検索は従来の公式掲載先を示すが、原本の掲載年度や地域金額は未確認。403の応答主体を確定しない。',
        retrieval_attempts=[r for r in attempts if '.meti.go.jp/' in r['url']],
        next_steps=['同条件で再試行せず、環境条件や公式掲載先の変更を確認した場合に再調査する。',
                    '2025/2026年度の交付決定・実績・評価を区別して原本照合する。'], **common),
        dict(ministry='内閣府', status='今回の現行案内は従来サイト・採択候補404継続',
        source_ids=[sid], requested_url=POLICY,
        note='現行政策ページは取得成功し、地方創生リンクが従来のchisou総合サイトを案内すると原本確認。案内先と2025/2026採択候補を各1回確認し404。移転・非公表・地域配分ゼロを認定せず、URL推測や同条件の再試行を打ち切った。',
        retrieval_attempts=[r for r in attempts if '.meti.go.jp/' not in r['url']],
        next_steps=['現行公式ページに新しい原本リンクが確認できた場合に取得する。',
                    '採択原本到達後も補正・当初、採択回、国費・事業費、支出階層を分離する。'], **common)]
    report = dict(schema_version=1, checked_at='2026-10-09', sources=[source], originals=[original], rows=[],
        research_notes=notes, retrieval_attempts=attempts, network_observation=observation,
        search_provenance=dict(provider='Exa', checked_at='2026-10-09',
            purpose='現行公式掲載先の探索のみ。検索結果は数値照合・原本未掲載の証明に使わない。',
            original_substitution=False),
        limitations=['ポリシー適用状態unknownは適用済みと認定しない。実際の取得成功・失敗を別記録にする。'])
    if OUTPUT.exists():
        previous = json.loads(OUTPUT.read_text())
        report['prior_reviews'] = previous.get('prior_reviews', [])
        if previous.get('retrieval_attempts') != attempts:
            report['prior_reviews'].append({k: previous[k] for k in
                ('checked_at', 'retrieval_attempts', 'network_observation', 'sources', 'originals')})
        if 'subsequent_network_observation' in previous:
            report['subsequent_network_observation'] = previous['subsequent_network_observation']
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


def validate():
    report = json.loads(OUTPUT.read_text())
    assert report['rows'] == []
    assert len(report['retrieval_attempts']) == len(URLS)
    assert report['network_observation']['proxy_and_tls_preserved']
    for receipt in report['retrieval_attempts']:
        checked(receipt['url'])
        assert receipt['network_observation'] == report['network_observation']
        if receipt['status'] == 'blocked_or_failed':
            assert 'sha256_original' not in receipt
        else:
            assert receipt['status'] == 'downloaded' and receipt['bytes'] > 0
    assert report['sources'][0]['excerpt'].endswith(REGIONAL)
    print(json.dumps(dict(attempts=len(URLS), comparison_rows=0, policy_state=report['network_observation']['network_policy_state'])))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--network-observation', type=Path)
    args = parser.parse_args()
    if args.fetch:
        fetch()
    if args.write:
        if not args.network_observation:
            parser.error('--write requires --network-observation')
        generate(args.network_observation)
    validate()
