"""Review FDMA initial-budget facility grant decisions, separately from spending.

Requires installed pdfplumber and Poppler for independent table/coordinate
extraction. Normal build/CI consume the saved JSON without these tools/network.
--fetch continues across failed URLs and preserves bounded failure receipts.
--write saves only successful, independently verified original amounts.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fetch_sources import MAX_BYTES, failure_details
from verify_grants_originals import table_rows, column, integer

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/other-ministry'
DATE = '2026-10-09'
URLS = {2025: 'https://www.fdma.go.jp/pressrelease/info/items/01_sisetu.pdf',
        2026: 'https://www.fdma.go.jp/pressrelease/info/items/R8sisetu.pdf'}
INDEXES = {2025: 'https://www.fdma.go.jp/pressrelease/info/2025/',
           2026: 'https://www.fdma.go.jp/pressrelease/info/'}


def sha(content):
    return hashlib.sha256(content).hexdigest()


def canonical_receipt(url, observation):
    """Retain reviewed retrieval evidence separately from cache observations."""
    report_path = ROOT / 'data/reviewed-fdma-facilities.json'
    if not report_path.exists():
        return observation
    report = json.loads(report_path.read_text())
    reviewed = next((item for item in report['originals'] if item['url'] == url), None)
    if reviewed is None:
        return observation
    fields = ('url', 'final_url', 'bytes', 'sha256_original', 'content_type')
    if any(observation.get(field) != reviewed.get(field) for field in fields):
        raise ValueError('Original differs from reviewed receipt; review changed content/metadata before publishing')
    # This also preserves the exact public retrieval_attempts schema, which
    # differs from the enriched originals schema. New times stay in manifest.
    attempt = next((item for item in report['retrieval_attempts']
                    if item['url'] == url and item['status'] == 'downloaded'), None)
    if attempt is None:
        raise ValueError('Reviewed original lacks successful public retrieval receipt')
    return dict(attempt)


def checked_fdma(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname != 'www.fdma.go.jp' or
            parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError('Not an approved official FDMA HTTPS resource')
    return url


class Redirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        depth = getattr(req, 'budget_redirects', 0)
        if depth >= 4:
            raise ValueError('Too many redirects')
        checked_fdma(newurl)
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if result is not None:
            result.budget_redirects = depth + 1
        return result


def original(url, fetch):
    checked_fdma(url)
    path = CACHE / (sha(url.encode()) + '.bin')
    manifest = CACHE / 'manifest.jsonl'
    receipts = [json.loads(line) for line in manifest.read_text().splitlines()] if manifest.exists() else []
    successful = [r for r in receipts if r['url'] == url and r.get('status') == 'downloaded']
    if path.exists() and successful:
        receipt = successful[-1]
        if sha(path.read_bytes()) != receipt['sha256_original'] or path.stat().st_size != receipt['bytes']:
            raise ValueError('Cached original digest/size differs from receipt')
        return path, canonical_receipt(url, receipt)
    if not fetch:
        return None, dict(url=url, status='not_cached', failure_category='original_not_collected')
    receipt = dict(url=url, retrieved_at_utc=datetime.now(timezone.utc).isoformat())
    try:
        req = Request(url, headers={'User-Agent': 'japan-budget-tracker/0.1 (public official budget research)'})
        with build_opener(Redirect()).open(req, timeout=25) as response:
            content = response.read(MAX_BYTES + 1)
            if len(content) > MAX_BYTES:
                raise ValueError('Original exceeds size limit')
            receipt.update(status='downloaded', final_url=checked_fdma(response.geturl()), bytes=len(content),
                           sha256_original=sha(content), content_type=response.headers.get_content_type())
        path.write_bytes(content)
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        receipt.update(status='blocked_or_failed', error_type=type(error).__name__, **failure_details(error))
        path = None
    with manifest.open('a') as stream:
        stream.write(json.dumps(receipt) + '\n')
    return path, canonical_receipt(url, receipt) if path is not None else receipt


def extract_tables(path, prefectures):
    import pdfplumber
    recipients, facilities = [], []
    total = None
    current_prefecture = current_recipient = None
    pending = []
    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, 1):
            tables = page.find_tables()
            if len(tables) != 1:
                raise ValueError('Expected one ruled table per PDF page')
            for raw in tables[0].extract():
                cells = [re.sub(r'\s+', '', value or '') for value in raw]
                if len(cells) != 6:
                    raise ValueError('Unexpected column cardinality')
                if cells[0] == '都道府県':
                    continue
                if '合計' in cells[:3]:
                    total = dict(quantity=integer(cells[3]), amount_thousand_yen=integer(cells[4]),
                                 recipient_sum_thousand_yen=integer(cells[5]))
                    continue
                if cells[0]:
                    if cells[0] not in prefectures:
                        raise ValueError('Unknown printed prefecture')
                    current_prefecture = cells[0]
                if cells[1]:
                    if pending:
                        raise ValueError('Recipient changed before subtotal')
                    current_recipient = cells[1]
                if cells[4] and re.fullmatch(r'\d+(?:,\d{3})*', cells[4]):
                    if not current_prefecture or not current_recipient or not cells[2]:
                        raise ValueError('Facility row lacks explicit recipient assignment')
                    facility = dict(prefecture=current_prefecture, recipient=current_recipient,
                                    facility=cells[2], quantity=integer(cells[3]),
                                    amount_thousand_yen=integer(cells[4]), page=page_number)
                    facilities.append(facility)
                    pending.append(facility)
                if cells[5] and re.fullmatch(r'\d+(?:,\d{3})*', cells[5]):
                    amount = integer(cells[5])
                    if amount != sum(f['amount_thousand_yen'] for f in pending):
                        raise ValueError('Facility amounts differ from recipient subtotal')
                    recipients.append(dict(prefecture=current_prefecture, recipient=current_recipient,
                                           amount_thousand_yen=amount, facilities=pending, page=page_number))
                    pending = []
        if pending or total is None:
            raise ValueError('Incomplete recipient/grand-total extraction')
    if (sum(r['amount_thousand_yen'] for r in recipients) != total['amount_thousand_yen'] or
            total['recipient_sum_thousand_yen'] != total['amount_thousand_yen'] or
            sum(f['quantity'] for f in facilities) != total['quantity']):
        raise ValueError('Recipient/facility sums or quantities differ from printed total')
    if len({(r['prefecture'], r['recipient']) for r in recipients}) != len(recipients):
        raise ValueError('Duplicate recipient, review any continuation/reprint')
    return recipients, facilities, total


def extract_poppler(path, year):
    """No ruled-table cells, pdfplumber strings, or geographic row assignments."""
    pending, recipients, grand = [], [], []
    for page, width, y, words in table_rows(path):
        name = column(words, width * .107, width * (.355 if year == 2025 else .338))
        if name and name != '市町村等' and '施設整備費補助金' not in name:
            pending.append(name)
        amount = column(words, width * .89, width)
        if re.fullmatch(r'\d+(?:,\d{3})*', amount):
            if pending:
                recipients.append((''.join(pending), integer(amount)))
                pending = []
            else:
                grand.append(integer(amount))
    if pending or len(grand) != 1:
        raise ValueError('Independent coordinate extraction has unresolved strings/totals')
    return recipients, grand[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    prefs = set(json.loads((ROOT / 'public/data.json').read_text())['prefectures'])
    attempts, available = [], {}
    # Independent requests are completed before extraction. A failed URL never
    # prevents another year's successfully retrieved original being checked.
    for url in list(INDEXES.values()) + list(URLS.values()):
        path, receipt = original(url, args.fetch)
        attempts.append(receipt)
        if path is not None:
            available[url] = path, receipt
    sources, originals, records, reconciliations = [], [], {}, {}
    for year, url in URLS.items():
        if url not in available:
            continue
        path, receipt = available[url]
        recipients, facilities, total = extract_tables(path, prefs)
        independently_extracted, independent_total = extract_poppler(path, year)
        if independently_extracted != [(r['recipient'], r['amount_thousand_yen']) for r in recipients] or independent_total != total['amount_thousand_yen']:
            raise ValueError('Independent Poppler recipient sequence/amounts differ from ruled-table extraction')
        expected_count = 91 if year == 2025 else 73
        if len(recipients) != expected_count:
            raise ValueError('Original recipient coverage changed; review before publishing')
        records[year] = {(r['prefecture'], r['recipient']): r for r in recipients}
        reconciliations[str(year)] = dict(recipients=len(recipients), facility_rows=len(facilities), total=total,
                                         facility_to_recipient_sums_equal=True, independent_poppler_equal=True)
        sid = f'fdma-facilities-first-{year}'
        date = '2025-05-09' if year == 2025 else '2026-04-21'
        # Dates/year/unit are independently checked in Poppler text, excluding
        # contact information from the committed source excerpt.
        import subprocess
        original_text = subprocess.check_output(['pdftotext', '-layout', str(path), '-'], text=True)
        compact = re.sub(r'\s+', '', original_text)
        expected_date = '令和7年5月9日' if year == 2025 else '令和8年4月21日'
        if expected_date not in compact or '（単位：件、千円）' not in compact:
            raise ValueError('Unexpected date or unit in original')
        excerpt = f'{date} 消防庁、{year}年度当初予算に係る消防防災施設整備費補助金交付決定一覧。単位：件、千円。\n'
        excerpt += '\n'.join(f'{r["prefecture"]} {r["recipient"]} 団体計 {r["amount_thousand_yen"]:,} ' +
                             '; '.join(f'{f["facility"]} 数量{f["quantity"]} 補助金額{f["amount_thousand_yen"]:,}' for f in r['facilities'])
                             for r in recipients)
        excerpt += f'\n合計 数量{total["quantity"]} 補助金額{total["amount_thousand_yen"]:,} 団体計{total["recipient_sum_thousand_yen"]:,}'
        locator = f'PDF1〜{7 if year == 2025 else 5}頁の都道府県・市町村等・団体計、施設別内訳'
        sources.append(dict(id=sid, title=f'{year}年度当初予算・消防防災施設整備費補助金交付決定一覧（初回掲載）',
                            url=url, locator=locator, kind='予算・議員資料', accessed=DATE, published=date,
                            retrieved_via='公式PDF原本の罫線セルと独立Poppler座標抽出を照合', excerpt=excerpt,
                            sha256_extracted_text=sha(excerpt.encode())))
        values = {f'fdma-facility-{pref}-{recipient}': {'amount' + str(year): r['amount_thousand_yen'] / 1000}
                  for (pref, recipient), r in records[year].items()}
        originals.append({**{k: v for k, v in receipt.items() if k != 'status'}, 'source_ids': [sid],
                          'download_status': 'downloaded', 'verification': dict(status='matched', checked_at=DATE,
                          method='罫線表セルと独立Poppler座標抽出で全掲載団体額・順序一致。全団体の施設別和と団体計、全国合計と数量に完全一致。',
                          locator=locator, row_ids=list(values), fields=['amount' + str(year)], values=values)})
        index_url = INDEXES[year]
        if index_url in available:
            index_path, index_receipt = available[index_url]
            html = index_path.read_text()
            link = re.search(r'<a[^>]+href=["\']' + re.escape(urlsplit(url).path) + r'["\'][^>]*>(.*?)</a>', html, re.S)
            if not link:
                raise ValueError('Selected PDF absent from official year index')
            label = re.sub(r'<[^>]+>', '', link[1]).strip()
            index_sid = f'fdma-facilities-index-{year}'
            sources.append(dict(id=index_sid, title=f'消防庁お知らせ：{year}年の当初交付決定リンク',
                                url=index_url, locator='消防防災施設整備費補助金の初回交付決定リンク', kind='地域配分索引',
                                accessed=DATE, published=None, retrieved_via='公式HTML原本のPDFリンクを確認',
                                excerpt=label + '\n' + url, sha256_extracted_text=sha((label + '\n' + url).encode())))
            originals.append({**{k: v for k, v in index_receipt.items() if k != 'status'}, 'source_ids': [index_sid],
                              'download_status': 'downloaded', 'verification': dict(status='downloaded_only', checked_at=DATE,
                              method='対象PDFリンクと初回・追加回の区別を確認', locator=sources[-1]['locator'], row_ids=[], fields=[], values={})})
    keys = set().union(*(set(table) for table in records.values())) if records else set()
    note = ('当初予算に係る初回掲載の交付決定額で、年度当初予算全体・全国の地域別支出済額ではない。'
            '2025年5月9日と2026年4月21日で決定日が異なり、追加回・変更・繰越分や対象施設量の同範囲性を未照合のため参考比較。'
            '施設別補助金額を団体計と独立照合し、親団体計と内訳を合算しない。'
            '片年度の原表非掲載はnullで保持し、ゼロ・廃止や年度全体の非採択とは認定しない。'
            '消防組合・広域行政組合の対象地域を単一市に割り当てず、原本の県・団体名を表示。市町村と小選挙区、決定時点の議員・党籍は未照合。')
    rows = []
    for prefecture, recipient in sorted(keys):
        row = dict(id=f'fdma-facility-{prefecture}-{recipient}', ministry='総務省', agency='消防庁',
                   program='消防防災施設整備費補助金', region=recipient, prefecture=prefecture,
                   basis='当初予算の交付決定・初回掲載（参考）', account='財源区分未確認', unit='百万円',
                   scope='原表掲載団体の交付決定額', period2025='2025年5月9日交付決定', period2026='2026年4月21日交付決定',
                   source_ids=[s['id'] for s in sources], evidence_status='公式原本集計済み', note=note,
                   geography_status='原本の都道府県・団体欄を確認。広域団体の構成市町村・小選挙区は未照合。')
        for year in (2025, 2026):
            original_row = records.get(year, {}).get((prefecture, recipient))
            row['amount' + str(year)] = original_row['amount_thousand_yen'] / 1000 if original_row else None
            row['absence_status' + str(year)] = ('not_listed_in_this_original' if year in records else 'original_not_collected') if original_row is None else None
        row['comparability'] = ('参考・決定日と対象施設の差' if all(row['amount' + str(y)] is not None for y in (2025, 2026))
                                else ('片年度非掲載' if len(records) == 2 else '片年度未取得'))
        rows.append(row)
    # The large Fujisawa increase has different printed target facilities, a
    # directly evidenced scope change. Individual decision rationale stays open.
    fujisawa = next((r for r in rows if r['region'] == '藤沢市'), None)
    if fujisawa and len(records) == 2:
        refs = ['fdma-facilities-first-2025', 'fdma-facilities-first-2026']
        fujisawa['explanation'] = '2025は耐震性貯水槽（60㎥型）1件6,897千円、2026は高機能消防指令センター総合整備事業（Ⅲ型）1件133,273千円。対象施設の変更を原本で確認。申請額・個別評価・完成状況や変更理由は未確認。増額だけで政治的働きかけを認定しない。'
        fujisawa['explanation_status'] = '原本の対象施設変更を確認・個別決定理由未確認'
        fujisawa['explanation_source_ids'] = refs
        fujisawa['driver_checks'] = [dict(dimension=dimension, status=status, finding=finding, source_ids=refs,
                                        locator='2025 PDF3頁・2026 PDF2頁の藤沢市欄') for dimension, status, finding in [
            ('申請額', '原本に非掲載', '掲載値は交付決定額。申請・要望額と充足率は両原本の当該団体欄に非掲載。'),
            ('採択基準', '未確認', '交付決定一覧は適正化法第6条第1項の根拠を記載するが、個別採点・採択基準適用結果を掲載しない。'),
            ('事業進捗', '原本に非掲載', '契約・着工・完成・支払の進捗を交付決定額から認定しない。'),
            ('制度・対象範囲', '対象範囲の差を確認', fujisawa['explanation']),
            ('決定記録', '交付決定一覧を確認', '当該原本の決定日・補助対象施設・数量・交付決定額を確認。個別通知書・審査記録・理由書は未確認。'),
            ('配分時点の関係者', '未確認', '当該時点の議員・首長・党籍・照会・要望や支持関係は独立照合していない。')]]
    report = dict(schema_version=1, checked_at=DATE, sources=sources, originals=originals, rows=rows,
                  retrieval_attempts=attempts, reconciliation=reconciliations,
                  listed_recipients={str(y): len(v) for y, v in records.items()},
                  research_notes=[dict(ministry='総務省', agency='消防庁', status='初回交付決定を参考収載・執行別',
                                       note=note, source_ids=[s['id'] for s in sources])])
    if args.write:
        for source in sources:
            (ROOT / 'data/source-text' / (source['id'] + '.txt')).write_text(source['excerpt'])
        (ROOT / 'data/reviewed-fdma-facilities.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    else:
        if report != json.loads((ROOT / 'data/reviewed-fdma-facilities.json').read_text()):
            raise ValueError('Re-extracted report differs from saved reviewed evidence; review before --write')
        for source in sources:
            if sha((ROOT / 'data/source-text' / (source['id'] + '.txt')).read_bytes()) != source['sha256_extracted_text']:
                raise ValueError('Saved extracted-text hash differs from reviewed source')
    print(json.dumps(dict(rows=len(rows), listed=report['listed_recipients'], reconciliation=reconciliations,
                          failed_requests=sum(r['status'] != 'downloaded' for r in attempts), written=args.write), ensure_ascii=False))


if __name__ == '__main__':
    main()
