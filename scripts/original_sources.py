"""Merge committed, reviewed original receipts; builds never access the network."""
import json
from collections import defaultdict


def apply_originals(root, sources, rows):
    receipts = []
    by_row = {r['id']: r for r in rows}
    for name in ('core-original-verification.json', 'maff-original-verification.json',
                 'grants-original-verification.json', 'reviewed-mlit.json',
                 'mof-structured-verification.json', 'monthly-original-verification.json',
                 'reviewed-mof-programs.json','reviewed-mext.json','reviewed-cfa.json',
                 'reviewed-environment.json','reviewed-reconstruction.json',
                 'reviewed-cabinet-regional.json','reviewed-party-expansion.json','reviewed-meti-regional.json',
                 'reviewed-regional-followup.json','reviewed-party-followup.json','reviewed-municipality-districts.json',
                 'reviewed-mlit-water.json','reviewed-special-execution.json','reviewed-national-drivers.json',
                 'reviewed-roster-followup.json','reviewed-party-third-stage.json',
                 'reviewed-retrieval-followup.json','reviewed-care-cities.json',
                 'reviewed-reconstruction-followup.json','reviewed-fdma-facilities.json',
                 'reviewed-fdma-criteria.json',
                 'reviewed-fdma-reconciliation.json','reviewed-caa-execution.json',
                 'other-ministry-scope-review.json','reviewed-party-fourth-stage.json',
                 'reviewed-roster-continued.json','reviewed-aichi-districts.json',
                 'reviewed-mlit-water-images.json','reviewed-mlit-water-utilities.json'):
        report = json.loads((root / 'data' / name).read_text())
        if name in ('reviewed-party-followup.json','reviewed-municipality-districts.json',
                    'reviewed-roster-followup.json','reviewed-party-third-stage.json',
                    'reviewed-roster-continued.json','reviewed-aichi-districts.json',
                    'reviewed-party-fourth-stage.json'):
            # Public source IDs differ from the fetch receipt IDs. Keep the
            # original receipts intact and map them only for this snapshot.
            for receipt in report['originals']:
                receipt['source_ids'] = [s['id'] for s in report['sources']
                                        if s['original']['source_id'] == receipt['source_id']]
                receipt['verification'] = dict(status='downloaded_only', checked_at=report['checked_at'],
                    method='氏名・院・当選選挙区／市町村・区割りの照合用原本。予算金額の照合は対象外。',
                    locator='検証JSONのoriginal_locationと照合用本文', row_ids=[], fields=[])
        receipts.extend(report['originals'])
        for check in report.get('comparisons', []):
            row = by_row[check['row_id']]
            assert row[f'amount{check["year"]}'] == check['original_amount'], check
            if check['status'] == 'not_listed_in_grant_table':
                row['comparability'] = '片年度非掲載'
                row['note'] += f' {check["year"]}年度原本の交付金表に当該事業は非掲載。交付金ゼロとは認定しない。'
        for check in report.get('checks', []):
            if 'original' in check and isinstance(check['original'], dict):
                assert by_row[check['row_id']][check['field']] == check['original']['amount_million_yen'], check
        for original_row in report.get('rows', []):
            for field in ('amount2025', 'amount2026'):
                assert by_row[original_row['id']][field] == original_row[field]
        for result in report.get('results', []):
            for check in result.get('comparisons', []):
                assert round(by_row[check['row_id']][check['field']] * 1000) == check['amount_thousand_yen'], check
    checked_fields = defaultdict(set)
    by_source = {s['id']: s for s in sources}
    inventory = {}
    for receipt in receipts:
        verification = receipt.get('verification')
        source_ids = receipt.get('source_ids', [receipt['source_id']] if 'source_id' in receipt else [])
        inventory[receipt['url']] = receipt
        if verification and verification['status'] == 'matched':
            for rid, fields in verification.get('values', {}).items():
                for field, value in fields.items():
                    assert by_row[rid][field] == value, (rid, field, value)
            for rid in verification.get('row_ids', []):
                checked_fields[rid].update(verification.get('fields', []))
        for sid in source_ids:
            if sid not in by_source:
                continue
            source = by_source[sid]
            if 'original' not in source:
                source['original'] = {key: receipt[key] for key in
                    ('url', 'final_url', 'retrieved_at_utc', 'sha256_original', 'bytes', 'content_type')}
                source['original'].update(status='downloaded', verifications=[])
            if verification:
                source['original']['verifications'].append(verification)
    for row in rows:
        fields = checked_fields[row['id']] & {'amount2025', 'amount2026'}
        if fields:
            row['original_verified_fields'] = sorted(fields)
        if fields == {'amount2025', 'amount2026'} and all(row[f] is not None for f in fields):
            row['evidence_status'] = '原本数値照合済み'
        elif fields:
            if fields == {'amount2025', 'amount2026'}:
                dash = any('原本ダッシュ' in row.get(f'amount_status{year}', '') for year in (2025, 2026))
                row['evidence_status'] = '原本照合済み・原本ダッシュあり' if dash else '原本照合済み・片年度非掲載あり'
            else:
                row['evidence_status'] = '原本一部照合済み'
        if row['program']=='特定防衛施設周辺整備調整交付金' and any(row[f] is None for f in ('amount2025','amount2026')):
            row['comparability'] = '片年度非掲載'
    return dict(downloaded_unique_urls=len(inventory),
                sources_with_original=sum('original' in s for s in sources),
                fully_verified_comparison_rows=sum(r['evidence_status'] == '原本数値照合済み' for r in rows),
                note='取得済みと数値照合済みを分離。原本取得日時はUTC、調査日は日本時間。未照合資料の取得日・所属党照合日は更新しない。')
