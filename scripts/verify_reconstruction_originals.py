"""Verify Fukushima acceleration-grant notification originals without mixing rounds."""
import argparse
import hashlib
import json
import re
import subprocess
import time
import unicodedata
from pathlib import Path
from urllib.request import Request, build_opener

from fetch_sources import CheckedRedirect, HOSTS, MAX_BYTES, checked

HOSTS.add('www.reconstruction.go.jp')
ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache' / 'originals'
OUTPUT = ROOT / 'data' / 'reviewed-reconstruction.json'
CHECKED_AT = '2026-10-09'
BASE = 'https://www.reconstruction.go.jp/files/user/topics/main-cat1/sub-cat1-17/'
DOCUMENTS = {
    64: BASE + '20250401_fukushimasaiseikasoku_No64_kanougaku.pdf',
    68: BASE + '20260401_fukushimasaiseikasoku_No68_kanougaku.pdf',
    69: BASE + '20260408_fukushimasaiseikasoku_No69_kanougaku.pdf',
    'overview': BASE + '20260408_kasokuka-gaiyou.pdf',
    'entry': 'https://www.reconstruction.go.jp/topics/cat-11/sub-cat1-17/',
}
DETAILS = {
    64: {'published': '2025-04-01', 'summary': [(50557, 37810), (47728, 36181), (199, 199), (1668, 834), (1, 1), (489, 244), (472, 350)],
         'hamadori_body_page': 20, 'hamadori_table_page': 21, 'hamadori_values': [489, 244]},
    68: {'published': '2026-04-01', 'summary': [(5895, 127), (4157, 104), (1236, 18), (502, 5)],
         'hamadori_body_page': 11, 'hamadori_table_page': 12, 'hamadori_values': [502, 5]},
    69: {'published': '2026-04-08', 'summary': [(23307, 17110), (20110, 15534), (2693, 1328), (2, 2), (502, 246)],
         'hamadori_body_page': 16, 'hamadori_table_page': 17, 'hamadori_values': [502, 246]},
}


def original_path(url):
    return CACHE / (hashlib.sha256(url.encode()).hexdigest() + '.bin')


def fetch(url):
    if original_path(url).exists():
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    receipt = dict(url=url, retrieved_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    with build_opener(CheckedRedirect()).open(Request(checked(url), headers={
        'User-Agent': 'japan-budget-tracker/0.1 (public official budget research)'}), timeout=30) as response:
        declared_size = response.headers.get('Content-Length')
        if declared_size and int(declared_size) > MAX_BYTES:
            raise ValueError('File exceeds size limit')
        content = response.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError('File exceeds size limit')
        receipt.update(status='downloaded', bytes=len(content), sha256_original=hashlib.sha256(content).hexdigest(),
                       final_url=checked(response.geturl()), content_type=response.headers.get_content_type())
        original_path(url).write_bytes(content)
    with (CACHE / 'manifest.jsonl').open('a') as stream:
        stream.write(json.dumps(receipt, ensure_ascii=False) + '\n')


def receipt_for(url):
    receipts = [json.loads(line) for line in (CACHE / 'manifest.jsonl').read_text().splitlines()]
    receipt = next(r for r in reversed(receipts) if r['url'] == url and r['status'] == 'downloaded')
    content = original_path(url).read_bytes()
    assert len(content) == receipt['bytes']
    assert hashlib.sha256(content).hexdigest() == receipt['sha256_original']
    return {k: receipt[k] for k in ['url', 'final_url', 'retrieved_at_utc', 'sha256_original', 'bytes', 'content_type']}


def pages_for(url):
    receipt_for(url)
    path = original_path(url)
    subprocess.run(['pdftotext', '-layout', str(path), str(path.with_suffix('.txt'))], check=True)
    return path.with_suffix('.txt').read_text().split('\f')


def normalized(text):
    return unicodedata.normalize('NFKC', text)


def no_contacts(page):
    return page.split('本件連絡先')[0].strip()


def build():
    sources, originals, notifications = [], [], []
    for notification_round in [64, 68, 69]:
        detail = DETAILS[notification_round]
        url = DOCUMENTS[notification_round]
        pages = pages_for(url)
        summary = normalized(pages[0])
        pairs = re.findall(r'事業費\s*([\d,]+)百万円[、\s]+国費\s*([\d,]+)百万円', summary)
        amounts = [tuple(int(v.replace(',', '')) for v in pair) for pair in pairs]
        assert amounts == detail['summary'], (notification_round, amounts)
        assert f'第{notification_round}回' in summary
        year, month, day = map(int, detail['published'].split('-'))
        assert f'令和{year - 2018}年{month}月{day}日' in re.sub(r'\s+', '', summary)
        body = no_contacts(pages[detail['hamadori_body_page'] - 1])
        table = pages[detail['hamadori_table_page'] - 1]
        assert '浜通り地域等産業発展' in table and '交付可能額【国費】' in table
        table_match = re.search(r'福\s*島\s*県\s+([\d,]+)\s+([\d,]+)', normalized(table))
        assert table_match
        hamadori_values = [int(v.replace(',', '')) for v in table_match.groups()]
        assert hamadori_values == detail['hamadori_values']
        if notification_round == 68:
            compact_body = re.sub(r'\s+', '', normalized(body))
            assert '暫定予算期間中に必要な額' in compact_body
            assert '交付可能額の残額' in compact_body
            assert '令和8年度予算成立後' in compact_body
        source_id = f'reconstruction-fukushima-notification-{notification_round}'
        excerpt = '\n\n'.join([pages[0].strip(), body, table.strip()])
        sources.append(dict(id=source_id, title=f'福島再生加速化交付金 第{notification_round}回交付可能額通知（通知回単独・年度比較不可）',
                            url=url, locator=f'PDF1頁（通知全体の国費）、PDF{detail["hamadori_body_page"]}〜{detail["hamadori_table_page"]}頁（浜通り事業本文・福島県別紙）',
                            kind='交付可能額通知・比較条件調査', accessed=CHECKED_AT, published=detail['published'],
                            retrieved_via='公式PDF原本取得、pdftotext -layoutによる対象頁抽出',
                            sha256_extracted_text=hashlib.sha256(excerpt.encode()).hexdigest(), excerpt=excerpt))
        observation_id = f'reconstruction-notification-{notification_round}-hamadori-fukushima'
        notifications.append(dict(id=observation_id, fiscal_year=year, published=detail['published'], notification_round=notification_round,
                                  program='福島再生加速化交付金（浜通り地域等産業発展環境整備事業）', prefecture='福島県',
                                  recipient='福島県', basis='暫定予算期間の交付可能額' if notification_round == 68 else '当該通知回の交付可能額',
                                  business_cost_million_yen=hamadori_values[0], national_cost_million_yen=hamadori_values[1],
                                  unit='百万円', source_ids=[source_id], is_annual_total=False, comparison_status='比較条件未確定'))
        originals.append(dict(**receipt_for(url), source_ids=[source_id], verification=dict(
            status='downloaded_only', checked_at=CHECKED_AT, method='各通知回の表頭日付、総括の事業費・国費全組、浜通り事業福島県表の2欄を抽出し照合。公表比較行への金額照合はなく、年度比較額としては採用しない。',
            locator=sources[-1]['locator'], row_ids=[], observation_ids=[observation_id], fields=[])))
    overview = pages_for(DOCUMENTS['overview'])[0].strip()
    assert '第３期復興・創生期間' in overview
    sources.append(dict(id='reconstruction-fukushima-overview-2026', title='福島再生加速化交付金の概要（2026年版）',
                        url=DOCUMENTS['overview'], locator='PDF1頁、制度メニューと第3期復興・創生期間', kind='制度説明',
                        accessed=CHECKED_AT, published=None, document_date_inferred_from_filename='2026-04-08',
                        retrieved_via='公式PDF原本取得、pdftotext -layout',
                        sha256_extracted_text=hashlib.sha256(overview.encode()).hexdigest(), excerpt=overview))
    originals.append(dict(**receipt_for(DOCUMENTS['overview']), source_ids=['reconstruction-fukushima-overview-2026'],
                          verification=dict(status='downloaded_only', checked_at=CHECKED_AT, method='制度概要の第3期復興・創生期間とメニューを確認。資料本文に公表日なし。', locator='PDF1頁', row_ids=[], fields=[])))
    entry_html = original_path(DOCUMENTS['entry']).read_text()
    for key, url in DOCUMENTS.items():
        if key != 'entry':
            assert url in entry_html, url
    originals.append(dict(**receipt_for(DOCUMENTS['entry']), source_ids=[], verification=dict(
        status='downloaded_only', checked_at=CHECKED_AT, method='公式制度入口原本のハッシュと3通知回・制度概要PDFリンクを照合', locator='HTML', row_ids=[], fields=[])))
    return dict(schema_version=1, checked_at=CHECKED_AT, sources=sources, originals=originals, rows=[],
                research_notes=[dict(ministry='復興庁', status='原本取得済・通知段階差で未比較',
                                     note='福島再生加速化交付金の2025年4月第64回、2026年4月第68回・第69回と制度概要を原本取得。2026年第68回は暫定予算期間中の国費、第69回は成立後の通知であり、2025年初回と同条件の年度比較は未成立。浜通り事業費502百万円が第68・69回で重複し、通知回合算で年度額を作らない。',
                                     source_ids=[s['id'] for s in sources], checked_at=CHECKED_AT)],
                notification_observations=notifications,
                comparison_assessment=dict(status='同条件の年度比較未成立', public_comparison_rows_added=0,
                    reasons=[
                        '2025年4月1日第64回と2026年4月1日第68回は同条件でない。第68回は暫定予算期間中に必要な国費だけを通知している。',
                        '2026年4月8日第69回を第64回と直接比較すると暫定期間の通知国費が含まれるか不明。メニュー構成・対象団体数も異なる。',
                        '第64回全体には福島健康不安対策事業・水産業共同利用施設復興促進整備事業が含まれるが、第69回全体には含まれない。未掲載をゼロに置き換えない。',
                        '浜通り事業の暫定通知は成立後に残額通知予定と明記。ただし第69回本文には暫定額控除後の残額との明示なし。5+246=251百万円は追加照合が必要な候補計算で、確定年度額として公表しない。',
                        '浜通りの事業費502百万円は第68回・第69回の双方に掲載される。事業費の合算は重複計上になるため禁止。'],
                    next_steps=[
                        '2026年第68回・第69回の国費が排他的な暫定分・残額であることを県の交付決定資料・事業計画又は追加公式説明で照合する。',
                        '帰還・移住等環境整備は第64回・第68回・第69回の要素事業、年間計画、財源区分の対応を作成する。単に通知回全体を加算しない。',
                        '2025/2026の年度当初・補正・追加通知を分離した上で、同じ制度メニュー・受取団体・決定段階の比較を再判定する。']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    if args.fetch:
        for url in DOCUMENTS.values():
            fetch(url)
    result = build()
    if args.write:
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    else:
        assert json.loads(OUTPUT.read_text()) == result, 'Reviewed research differs from original re-extraction'
    print(json.dumps({'sources': len(result['sources']), 'originals': len(result['originals']),
                      'notification_observations': len(result['notification_observations']),
                      'comparison_rows': len(result['rows']), 'status': result['comparison_assessment']['status']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
