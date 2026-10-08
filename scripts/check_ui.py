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
    page.click('[data-view=national]')
    assert page.locator('#row-count').inner_text()=='19'
    page.click('[data-view=special]')
    assert page.locator('#row-count').inner_text()=='34'
    page.click('[data-view=execution]')
    assert page.locator('#row-count').inner_text()=='36'
    page.select_option('#program','所管別支出済歳出額（7月末累計）')
    assert page.locator('#row-count').inner_text()=='18'
    assert '4〜7月' in page.locator('#rows').inner_text()
    page.click('[data-view=reference]')
    page.check('#candidate-only')
    assert page.locator('#candidate-count').inner_text()=='0'
    page.click('[data-view=coverage]')
    assert '復興庁' in page.locator('#coverage').inner_text()
    page.select_option('#member-pref','鳥取県')
    assert '鳥取' in page.locator('#members').inner_text() and '島根' in page.locator('#members').inner_text()
    page.select_option('#member-pref','山形県')
    assert '国民民主党' in page.locator('#members').inner_text()
    assert '所属資料の基準日：未確認' in page.locator('#members').inner_text()
    page.select_option('#member-pref','')
    assert page.locator('#members .member').count()==711
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
