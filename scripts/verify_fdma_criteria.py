"""Review FY2026 facility-grant rules without inferring individual assessment.

Default validates committed evidence offline. --extract uses ignored originals
and compares Poppler extraction with independent pdfplumber text for each cited
clause. --fetch makes bounded requests through the inherited proxy and verified
TLS; it does not retry failed requests. --write requires --extract.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess

from verify_fdma_facilities import original

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'data/reviewed-fdma-criteria.json'
URLS = {
    'fdma-criteria-2026': 'https://www.fdma.go.jp/about/others/items/02_shisetuhojokinyoukou.pdf',
    'fdma-guide-2026': 'https://www.fdma.go.jp/about/others/items/r8_tebiki.pdf',
}
# Exact excerpts are bounded to rules, excluding sample forms/contact details.
SELECTIONS = {
    'fdma-criteria-2026': [
        (1, '最終改正', '（通則）'),
        (3, '（補助率）', '２ 交付申請書'),
        (4, '３ 都道府県知事', '（交付の条件）'),
        (6, '（実績報告）', '（実績報告書の提出期限）'),
        (12, '附 則（令和８年４月８日', ''),
    ],
    'fdma-guide-2026': [(47, '６-４', '')],
}
CLAUSES = {
    'fdma-criteria-2026': {
        1: ['最終改正令和８年４月８日消防消第123号'],
        3: ['別表第１又は別表第２に定める基準額の３分の１以内',
            '市町村にあっては都道府県知事を経由して消防庁長官に提出'],
        4: ['交付申請書を受理したときは内容を審査し',
            '交付の申請があった地方公共団体に対して交付決定の通知'],
        6: ['補助事業を完了し、又は廃止した場合', '実績報告書'],
        12: ['この要綱は、令和８年度分の補助金から適用する'],
    },
    'fdma-guide-2026': {47: ['標準仕様書に基づき整備（新設・更新）',
                            '【補助率】1/3', '消防指令システム整備分のみ対象']},
}


def sha(content):
    return hashlib.sha256(content).hexdigest()


def compact(value):
    return re.sub(r'\s+', '', value)


def extract(path, sid):
    import pdfplumber
    pages = subprocess.run(['pdftotext', '-layout', str(path), '-'],
                           check=True, capture_output=True, text=True).stdout.split('\f')
    excerpts = []
    with pdfplumber.open(path) as pdf:
        for number, start, end in SELECTIONS[sid]:
            page = ' '.join(pages[number - 1].split())
            first = page.index(start)
            last = page.index(end, first) if end else len(page)
            text = page[first:last].strip()
            independent = compact(pdf.pages[number - 1].extract_text())
            for clause in CLAUSES[sid][number]:
                assert compact(clause) in compact(pages[number - 1]), (sid, number, clause)
                assert compact(clause) in independent, (sid, number, clause)
            excerpts.append(f'PDF{number}頁：{text}')
    return '\n'.join(excerpts)


def generate(fetch):
    saved = json.loads(OUTPUT.read_text()) if OUTPUT.exists() else None
    sources, originals = [], []
    for sid, url in URLS.items():
        path, receipt = original(url, fetch)
        if path is None:
            raise ValueError('Rules original unavailable; no individual-rule conclusions published')
        if saved:
            previous = next(o for o in saved['originals'] if o['url'] == url)
            for field in ('url', 'final_url', 'sha256_original', 'bytes', 'content_type'):
                assert previous[field] == receipt[field], 'Changed original requires explicit review'
            receipt['retrieved_at_utc'] = previous['retrieved_at_utc']
        excerpt = extract(path, sid)
        locator = ('PDF1頁の改正日、3〜4頁の第6〜9条、6頁の第13条、12頁の2026適用附則'
                   if sid == 'fdma-criteria-2026' else 'PDF47頁（冊子30頁）の6-4：高機能消防指令センター')
        sources.append(dict(id=sid, title=('消防防災施設整備費補助金交付要綱・2026適用版'
                            if sid == 'fdma-criteria-2026' else '2026年度消防防災施設・設備整備の財政措置活用の手引き'),
            url=url, locator=locator, kind='配分基準・制度', accessed='2026-10-09',
            published=None,
            retrieved_via='公式PDFのPoppler抽出とpdfplumber別抽出で引用条項を照合',
            excerpt=excerpt, sha256_extracted_text=sha(excerpt.encode())))
        originals.append({**{k: receipt[k] for k in ('url', 'final_url', 'retrieved_at_utc',
                'sha256_original', 'bytes', 'content_type')}, 'source_ids': [sid],
                'verification': dict(status='downloaded_only', checked_at='2026-10-09',
                    method='引用条項を独立テキスト抽出で照合。予算金額・個別採点の照合ではない。',
                    locator=locator, row_ids=[], fields=[])})
    old = next(r for r in json.loads((ROOT / 'data/reviewed-fdma-facilities.json').read_text())['rows']
               if r['region'] == '藤沢市')
    checks = copy.deepcopy(old['driver_checks'])
    rules = list(URLS)
    checks[1].update(status='2026年度一般基準を確認・個別適用未確認',
        finding='2026年4月8日改正要綱は2026年度分から適用。第6条は原則として基準額の1/3以内（施設別例外あり）。高機能指令センターは手引きで指令システム整備分のみ対象・標準仕様書による新設/更新を確認。藤沢市の基準額、申請額、個別審査・要件充足、2025適用版との差は未確認。交付額を1/3で逆算して総事業費・申請額と認定しない。',
        source_ids=checks[1]['source_ids'] + rules,
        locator='2026要綱第6条・適用附則、2026手引き6-4')
    checks[4].update(status='交付決定一覧・一般手続を確認・個別審査記録未確認',
        finding=checks[4]['finding'] + ' 2026要綱第7〜9条は市町村の知事経由申請、知事の内容審査・調書、消防庁長官の決定通知を規定。第13条は完了/廃止後の実績報告を規定するが、藤沢市の申請書・交付通知・実績報告は未取得。',
        source_ids=checks[4]['source_ids'] + rules[:1], locator='2026要綱第7〜9条・第13条')
    return dict(schema_version=1, checked_at='2026-10-09', sources=sources, originals=originals, rows=[],
        row_enrichments=[dict(row_id=old['id'], driver_checks=checks,
                             prior_driver_checks=old['driver_checks'], explanation_source_ids=rules)],
        research_notes=[dict(ministry='総務省', agency='消防庁', status='2026適用要綱・一般基準と手続を照合',
            source_ids=rules, note='4月8日改正の2026適用附則を確認。原則基準額1/3以内、指令システムの対象範囲・標準仕様、申請/決定/実績報告手続を照合。手引きは2026年5月版。個別採点・申請額・契約/支出・関係者と2025適用版との差は未確認。補助率は総工事費の比率と同一視しない。')])


def validate(report):
    assert report['rows'] == [] and len(report['sources']) == len(report['originals']) == 2
    assert len(report['row_enrichments']) == 1
    for source in report['sources']:
        assert source['url'] == URLS[source['id']]
        assert sha(source['excerpt'].encode()) == source['sha256_extracted_text']
        assert (ROOT / 'data/source-text' / (source['id'] + '.txt')).read_text() == source['excerpt']
    for o in report['originals']:
        assert o['verification']['fields'] == [] and o['verification']['row_ids'] == []
    checks = report['row_enrichments'][0]['driver_checks']
    assert len(checks) == 6
    assert '個別適用未確認' in checks[1]['status']
    assert '実績報告は未取得' in checks[4]['finding']
    print(json.dumps(dict(sources=2, comparison_rows=0, individual_assessment='unverified')))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--extract', action='store_true')
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    if args.fetch and not args.extract:
        parser.error('--fetch requires --extract')
    if args.write and not args.extract:
        parser.error('--write requires --extract')
    if args.extract:
        result = generate(args.fetch)
        if args.write:
            OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
            for s in result['sources']:
                (ROOT / 'data/source-text' / (s['id'] + '.txt')).write_text(s['excerpt'])
        else:
            assert result == json.loads(OUTPUT.read_text()), 'Re-extracted rules differ from reviewed evidence'
    validate(json.loads(OUTPUT.read_text()))
