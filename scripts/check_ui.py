"""Optional rendered verification; requires preinstalled Playwright and Chromium."""
import json
import os
import shutil
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.cache';OUT.mkdir(exist_ok=True)
with sync_playwright() as p:
    executable=os.environ.get('BUDGET_BROWSER_PATH') or shutil.which('chromium')
    browser=p.chromium.launch(headless=True,executable_path=executable)
    page=browser.new_page(viewport={'width':1440,'height':1080})
    errors=[];remote=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('console',lambda m:errors.append(m.text) if m.type=='error' else None)
    page.on('request',lambda r:remote.append(r.url) if not r.url.startswith('http://127.0.0.1:8000/') and not r.url.startswith('blob:') else None)
    page.goto('http://127.0.0.1:8000/',wait_until='networkidle')
    page.wait_for_function("document.getElementById('row-count').textContent !== '—'")
    assert not page.locator('#error').is_visible()
    page.screenshot(path=str(OUT/'dashboard-wide.png'),full_page=True)
    page.select_option('#prefecture','栃木県')
    page.fill('#query','那珂川')
    assert page.locator('#rows').inner_text().count('那珂川町')==3
    page.locator('#rows button').first.click()
    assert page.locator('#detail').is_visible()
    assert '簗和生' in page.locator('#detail-content').inner_text()
    assert '渡辺真太朗' in page.locator('#detail-content').inner_text()
    assert '区割り原本照合・市町村全域' in page.locator('#detail-content').inner_text()
    assert '申請額：原本に非掲載' in page.locator('#detail-content').inner_text()
    assert '増減理由と決定過程の確認状態' in page.locator('#detail-content').inner_text()
    page.locator('#detail-content details summary').first.click()
    assert '原本ファイルのハッシュではありません' in page.locator('#detail-content').inner_text()
    page.locator('#detail-content .original-receipt summary').first.click()
    assert 'SHA-256（原本ファイル）' in page.locator('#detail-content').inner_text()
    assert '原本数値照合済み' in page.locator('#detail-content').inner_text()
    page.keyboard.press('Escape')
    page.click('#reset')
    page.select_option('#prefecture','愛知県')
    page.select_option('#program','普通交付税')
    page.check('#candidate-only')
    assert page.locator('#rows tr').count()==1
    assert '公式説明あり' in page.locator('#rows').inner_text()
    with page.expect_download() as download:
        page.click('#export')
    downloaded=download.value;downloaded.save_as(str(OUT/'export.csv'))
    assert 'aichi.jp' in (OUT/'export.csv').read_text(encoding='utf-8-sig')
    assert 'legislator_evidence' in (OUT/'export.csv').read_text(encoding='utf-8-sig')
    page.click('#reset')
    page.select_option('#prefecture','静岡県')
    page.select_option('#program','農山漁村地域整備交付金')
    assert page.locator('#row-count').inner_text()=='1'
    assert '2,376' in page.locator('#rows').inner_text()
    page.click('#reset')
    page.select_option('#program','道路関係補助事業（事業費・国費ではない）')
    assert page.locator('#row-count').inner_text()=='47'
    with page.expect_download() as download:
        page.click('#export')
    download.value.save_as(str(OUT/'road-export.csv'))
    exported=(OUT/'road-export.csv').read_text(encoding='utf-8-sig')
    assert 'sha256_original' in exported and '地方負担' in exported
    assert '道路関係直轄事業' not in exported
    page.select_option('#program','道路関係直轄事業（事業費・国費ではない）')
    assert page.locator('#row-count').inner_text()=='10'
    page.locator('#rows button').first.click()
    assert '地域への割当なし' in page.locator('#detail-content').inner_text()
    assert '原本数値照合済み' in page.locator('#detail-content').inner_text()
    page.keyboard.press('Escape')
    page.click('#reset')
    page.select_option('#program','上下水道関係補助事業（事業費・国費ではない）')
    assert page.locator('#row-count').inner_text()=='47'
    page.fill('#query','青森県')
    assert '原本ダッシュ' in page.locator('#rows').inner_text()
    page.fill('#query','東京都')
    page.locator('#rows button').first.click()
    assert '3,131' in page.locator('#detail-content').inner_text()
    assert '工事諸費' in page.locator('#detail-content').inner_text()
    page.keyboard.press('Escape')
    page.fill('#query','')
    page.select_option('#program','水道関係補助事業（事業費・国費ではない）')
    assert page.locator('#row-count').inner_text()=='47'
    page.select_option('#program','河川関係補助事業（事業費・国費ではない）')
    page.locator('#rows button').first.click()
    page.get_by_text('比較範囲の確認履歴',exact=True).click()
    assert '前の表示を履歴として保持' in page.locator('#detail-content').inner_text()
    assert '補助の工事諸費の扱いは未確認' in page.locator('#detail-content').inner_text()
    page.keyboard.press('Escape')
    with page.expect_download() as download:
        page.click('#export')
    download.value.save_as(str(OUT/'water-scope-export.csv'))
    assert 'scope_review' in (OUT/'water-scope-export.csv').read_text(encoding='utf-8-sig')
    assert 'prior' in (OUT/'water-scope-export.csv').read_text(encoding='utf-8-sig')
    page.click('#reset')
    page.click('[data-view=national]')
    assert page.locator('#row-count').inner_text()=='19'
    page.click('[data-view=programs]')
    assert page.locator('#row-count').inner_text()=='859'
    assert '制度の連続性は未確認' in page.locator('#view-note').inner_text()
    with page.expect_download() as download:
        page.click('#export')
    download.value.save_as(str(OUT/'program-export.csv'))
    program_export=(OUT/'program-export.csv').read_text(encoding='utf-8-sig')
    assert '片年度非掲載' in program_export and '制度連続性未確認' in program_export
    page.check('#candidate-only')
    assert page.locator('#candidate-count').inner_text()=='0'
    page.click('#reset')
    page.click('[data-view=institutions]')
    assert page.locator('#row-count').inner_text()=='86' and page.locator('#prefecture').is_disabled()
    page.locator('#rows button').first.click()
    assert '交付決定額' in page.locator('#detail-content').inner_text()
    assert '地域への割当なし' in page.locator('#detail-content').inner_text()
    page.keyboard.press('Escape')
    page.click('[data-view=special]')
    assert page.locator('#row-count').inner_text()=='34'
    page.click('[data-view=execution]')
    assert page.locator('#row-count').inner_text()=='104'
    page.select_option('#program','所管別支出済歳出額（7月末累計）')
    assert page.locator('#row-count').inner_text()=='18'
    assert '4〜7月' in page.locator('#rows').inner_text()
    page.select_option('#program','')
    page.fill('#query','先端半導体')
    assert page.locator('#row-count').inner_text()=='2'
    assert '原本ダッシュ（ゼロ認定なし）' in page.locator('#rows').inner_text()
    page.locator('#rows button').first.click()
    assert '原本ダッシュあり' in page.locator('#detail-content').inner_text()
    page.keyboard.press('Escape')
    page.fill('#query','')
    page.click('[data-view=programs]')
    page.fill('#query','教育政策推進費')
    page.locator('#rows button').first.click()
    assert '164,882.856' in page.locator('#detail-content').inner_text()
    assert '個別地域配分未確認' in page.locator('#detail-content').inner_text()
    page.keyboard.press('Escape')
    page.fill('#query','')
    page.click('[data-view=regional]')
    page.select_option('#program','港湾・港湾海岸補助事業（事業費・国費ではない）')
    assert page.locator('#row-count').inner_text()=='39'
    page.fill('#query','福島')
    assert '非掲載' in page.locator('#rows').inner_text()
    page.fill('#query','')
    page.click('[data-view=reference]')
    page.select_option('#program','子ども・子育て支援施設整備交付金')
    assert page.locator('#row-count').inner_text()=='330'
    missing_cfa=next(r for r in json.loads((ROOT/'public/data.json').read_text())['rows']
                     if r['program']=='子ども・子育て支援施設整備交付金' and r['amount2025'] is None)
    page.fill('#query',missing_cfa['region'])
    assert '非掲載' in page.locator('#rows').inner_text()
    page.fill('#query','')
    page.select_option('#program','特定防衛施設周辺整備調整交付金（第3回）')
    assert page.locator('#row-count').inner_text()=='122'
    assert '未収載・同回原本未確認' in page.locator('#rows').inner_text()
    page.check('#candidate-only')
    assert page.locator('#candidate-count').inner_text()=='0'
    page.uncheck('#candidate-only')
    page.fill('#query','宇都宮市')
    page.locator('#rows button').first.click()
    split_text=page.locator('#detail-content').inner_text()
    assert '旧宇都宮市の区域' in split_text and '旧上河内町・旧河内町の区域' in split_text
    assert '[object Object]' not in split_text
    page.keyboard.press('Escape')
    page.fill('#query','')
    page.select_option('#program','循環型社会形成推進交付金等（4月内示掲載額合計）')
    assert page.locator('#row-count').inner_text()=='47'
    page.check('#candidate-only')
    assert page.locator('#candidate-count').inner_text()=='0'
    page.uncheck('#candidate-only')
    page.select_option('#program','地域介護・福祉空間整備等施設整備交付金')
    assert page.locator('#row-count').inner_text()=='129'
    page.fill('#query','名古屋市')
    page.locator('#rows button').first.click()
    city_text=page.locator('#detail-content').inner_text()
    assert '自治体コード：231002' in city_text and '今回計画額' in city_text and '計画額（千円）' in city_text
    assert '愛知1・愛知2・愛知3・愛知4・愛知5' in city_text
    page.keyboard.press('Escape')
    page.fill('#query','')
    page.select_option('#program','消防防災施設整備費補助金')
    assert page.locator('#row-count').inner_text()=='124'
    page.fill('#query','藤沢市')
    page.locator('#rows button').first.click()
    assert 'Ⅲ型' in page.locator('#detail-content').inner_text()
    assert '配分時点の関係者' in page.locator('#detail-content').inner_text()
    assert '2026年度一般基準を確認・個別適用未確認' in page.locator('#detail-content').inner_text()
    assert '交付額を1/3で逆算して総事業費・申請額と認定しない' in page.locator('#detail-content').inner_text()
    page.keyboard.press('Escape')
    page.fill('#query','')
    page.select_option('#program','地方消費者行政強化交付金')
    assert page.locator('#row-count').inner_text()=='47'
    assert '未収載' in page.locator('#rows').inner_text()
    page.locator('#rows button').first.click()
    assert '原本数値照合済み' in page.locator('#detail-content').inner_text()
    assert '国の支出済額' in page.locator('#detail-content').inner_text()
    assert '片年度未収載' in page.locator('#detail-content').inner_text()
    page.keyboard.press('Escape')
    page.fill('#query','')
    page.select_option('#program','福島再生加速化交付金（浜通り地域等産業発展環境整備事業）公表県事業計画')
    assert page.locator('#row-count').inner_text()=='1'
    page.locator('#rows button').first.click()
    assert '251.187' in page.locator('#detail-content').inner_text()
    assert '通知前原申請額' in page.locator('#detail-content').inner_text()
    page.keyboard.press('Escape')
    page.click('[data-view=coverage]')
    assert '復興庁' in page.locator('#coverage').inner_text()
    assert '通知段階差で未比較' in page.locator('#coverage').inner_text()
    assert 'HTTP 403・原本未取得' in page.locator('#coverage').inner_text()
    assert '原本内不一致未解消' in page.locator('#coverage').inner_text()
    assert '127観測' in page.locator('#coverage').inner_text()
    page.get_by_text('原本取得済・通知段階差で未比較',exact=True).click()
    assert '先行調査（06d7543）の記録' in page.locator('#coverage').inner_text()
    assert '今回の現行案内' in page.locator('#coverage').inner_text()
    page.get_by_text('決定概要と当初・繰越の範囲差を金額・件数で照合',exact=True).click()
    page.get_by_text('消防庁・施設補助金：当初と繰越の範囲差を照合',exact=True).click()
    assert '48.272' in page.locator('#coverage').inner_text()
    assert '地域受領額非掲載' in page.locator('#coverage').inner_text()
    page.get_by_text('年度比較に採用していない通知の原表：127観測',exact=True).click()
    tamura=page.locator('#coverage tbody tr').filter(has_text='第64回').filter(has_text='田村市')
    assert tamura.count()==1
    tamura.locator('button').click()
    assert '原本内部の整合性' in page.locator('#detail-content').inner_text()
    page.keyboard.press('Escape')
    page.select_option('#member-pref','鳥取県')
    assert '鳥取' in page.locator('#members').inner_text() and '島根' in page.locator('#members').inner_text()
    page.select_option('#member-pref','山形県')
    assert '国民民主党' in page.locator('#members').inner_text()
    assert '所属資料の基準日：未確認' in page.locator('#members').inner_text()
    page.select_option('#member-pref','')
    assert page.locator('#members .member').count()==711
    assert '減税日本（地域政党・政治団体の所属確認）' in page.locator('#members').inner_text()
    assert '選挙時公認の別記録' in page.locator('#members').inner_text()
    assert '氏名対応保留' in page.locator('#members').inner_text()
    assert '旧名簿・逝去を公式確認' in page.locator('#members').inner_text()
    page.locator('#held-members > details > summary').click()
    assert '栗原渉' in page.locator('#held-members').inner_text()
    assert '旧名簿IDに未一致・新原本独立確認' in page.locator('#held-members').inner_text()
    assert '資料間不一致（中道改革連合／公明党）' in page.locator('#members').inner_text()
    coverage=json.loads((ROOT/'public/data.json').read_text())['party_coverage']
    assert f"{coverage['verified']} / 711人" in page.locator('#party-coverage').inner_text()
    page.select_option('#member-pref','栃木県')
    page.click('[data-view=regional]');page.click('#reset')
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path=str(OUT/'dashboard-mobile.png'),full_page=True)
    assert not errors,errors
    assert not remote,remote
    browser.close()
    print(json.dumps({'desktop':'1440x1080','mobile':'390x844','checks':['filters','details','MLIT scopes','Aichi explanation','CSV export with party evidence','national','Q1 and July cumulative','noncomparable exclusion','coverage','joint Senate district','nationwide legislators','party sources and conflicts','responsive overflow','no console/CSP errors','no external requests']},ensure_ascii=False))
