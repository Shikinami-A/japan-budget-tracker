"""Build a reviewed, dependency-free snapshot. Raw research is never published."""
import hashlib
import json
import re
from pathlib import Path
from party_sources import apply_party_rosters
from original_sources import apply_originals

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'data' / 'source-text'
DATE = '2026-10-09'
EXTRACT_DATE = '2026-10-08'
PREFS = '北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 栃木県 群馬県 埼玉県 千葉県 東京都 神奈川県 新潟県 富山県 石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県 三重県 滋賀県 京都府 大阪府 兵庫県 奈良県 和歌山県 鳥取県 島根県 岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県'.split()
SHORT = {p if p == '北海道' else p[:-1]: p for p in PREFS}
SOURCES = []
ROWS = []


def source(id, file, title, url, locator, excerpt=None, published=None, kind='予算・議員資料'):
    raw = (RAW / file).read_text()
    text = excerpt if excerpt is not None else raw
    # Only selected public evidence, not entire reports, personal contacts or news copies.
    SOURCES.append(dict(id=id, title=title, url=url, locator=locator, kind=kind,
                        accessed=DATE if id=='maff-2026-49' else EXTRACT_DATE, published=published,
                        retrieved_via='公式原本PDFから該当表を抽出' if id=='maff-2026-49' else 'Exaによる公式本文抽出',
                        sha256_extracted_text=hashlib.sha256(text.encode()).hexdigest(),
                        excerpt=text))
    return raw


def row(id, ministry, program, region, basis, a, b, sources, **extra):
    ROWS.append(dict(id=id, ministry=ministry, program=program, region=region,
                     prefecture=extra.pop('prefecture', region if region in PREFS else None),
                     basis=basis, account=extra.pop('account', '一般会計'),
                     amount2025=a, amount2026=b, unit='百万円',
                     source_ids=sources, period2025=extra.pop('period2025', '2025年度'),
                     period2026=extra.pop('period2026', '2026年度'),
                     evidence_status='公式本文確認', comparability=extra.pop('comparability', '同範囲'),
                     **extra))


def main():
    text = (RAW / 'source-0.txt').read_text()
    start = text.rindex('3 令和 8 年度一般会計歳出予算所管別対前年度比較表')
    excerpt = text[start:].split('4 令和 8 年度予算定員')[0]
    source('mof-initial', 'source-0.txt', '2026年度予算及び財政投融資計画の説明',
           'https://www.mof.go.jp/policy/budget/budger_workflow/budget/fy2026/tousyoyosetsu.pdf',
           '付表3、本文101頁、単位：千円', excerpt)
    source('mof-enacted', 'source-4-0.txt', '2026年度予算成立',
           'https://www.mof.go.jp/policy/budget/budger_workflow/budget/fy2026/index.html',
           '予算成立：2026年4月7日、政府案どおり成立',
           '令和８年４月７日 令和８年度予算は政府案どおり成立しました。', '2026-04-07')
    for line in excerpt.splitlines():
        if not line.startswith('|') or not re.search(r'\d,\d', line):
            continue
        cells = [c.strip() for c in line.split('|')[1:-1]]
        first_num = next((i for i, c in enumerate(cells) if re.fullmatch(r'[\d,]+', c)), None)
        if first_num is None:
            continue
        name = ''.join(cells[:first_num]).replace(' ', '')
        numbers = re.findall(r'\d[\d,]*', '|'.join(cells[first_num:]))
        if name == '防災庁':
            vals = [int(numbers[0].replace(',', '')), 0, 0]
        elif len(numbers) >= 3:
            vals = [int(n.replace(',', '')) for n in numbers[:3]]
        else:
            continue
        if name == '合計':
            assert vals[0] == 122309247035 and vals[1] == 115197845248
            continue
        for basis, old in [('当初予算', vals[1]), ('2025補正後対2026当初（非対称）', vals[2])]:
            row(f'national-{len(ROWS)}', name, '所管総額', '全国', basis,
                old / 1000, vals[0] / 1000, ['mof-initial', 'mof-enacted'],
                comparability='同範囲' if basis == '当初予算' else '参考・非対称比較',
                note='一般会計のみ。内閣府にはこども家庭庁などを含む。特別会計・地域配分は別枠。'+
                     (' 2025年度は防災庁の独立所管項目がないため前年0。防災施策全体の予算や支出が0という意味ではない。' if name=='防災庁' else ''))
    special=(RAW/'special-accounts.txt').read_text()
    source('mof-special','special-accounts.txt','2026年度予算説明：特別会計歳入歳出予算',
           'https://www.mof.go.jp/policy/budget/budger_workflow/budget/fy2026/tousyoyosetsu.pdf',
           '本文13〜14頁、歳出欄、単位：千円。括弧内は2025年度当初。',special)
    # Reviewed transcription of the expenditure columns; no income/expense mixing.
    special_values=[
        ('交付税及び譲与税配付金','総額',50719300171,51065056703),
        ('地震再保険','総額',117427430,126339766),
        ('国債整理基金','総額',222118500012,224828567066),
        ('外国為替資金','総額',1545794724,1973396598),
        ('財政投融資','財政融資資金勘定',21638208346,26676473344),
        ('財政投融資','投資勘定',851790046,758500041),
        ('財政投融資','特定国有財産整備勘定',4072404,4377380),
        ('エネルギー対策','エネルギー需給勘定',2984637341,3241496260),
        ('エネルギー対策','電源開発促進勘定',420929444,473227391),
        ('エネルギー対策','原子力損害賠償支援勘定',12169336286,11967206114),
        ('エネルギー対策','先端半導体・人工知能関連技術勘定',332800000,1239004417),
        ('労働保険','労災勘定',1106427217,1122144571),
        ('労働保険','雇用勘定',2191667055,2287590081),
        ('労働保険','徴収勘定',4257725436,4224165321),
        ('年金','基礎年金勘定',28557395255,28980031698),
        ('年金','国民年金勘定',4312450059,4238936181),
        ('年金','厚生年金勘定',52432946234,53781662807),
        ('年金','健康勘定',13768540281,14640093254),
        ('年金','業務勘定',1246216154,1285836031),
        ('子ども・子育て支援','子ども・子育て支援勘定',4711372242,4796876921),
        ('子ども・子育て支援','育児休業等給付勘定',1068697363,1104257967),
        ('食料安定供給','農業経営安定勘定',247250231,239451358),
        ('食料安定供給','食糧管理勘定',939840290,905209426),
        ('食料安定供給','農業再保険勘定',96801256,93361649),
        ('食料安定供給','漁船再保険勘定',6878861,6681619),
        ('食料安定供給','漁業共済保険勘定',11501857,12188667),
        ('食料安定供給','業務勘定',24114989,31040437),
        ('食料安定供給','国営土地改良事業勘定',8419830,6271017),
        ('国有林野事業債務管理','総額',334695285,330539496),
        ('特許','総額',154397913,160632536),
        ('自動車安全','自動車事故対策勘定',22290127,23854986),
        ('自動車安全','自動車検査登録勘定',43579760,44581554),
        ('自動車安全','空港整備勘定',388981361,423591419),
        ('東日本大震災復興','総額',646243623,633368369)]
    for account,sub,a,b in special_values:
        assert f'{a:,}' in special and f'{b:,}' in special
        row(f'special-{account}-{sub}','特別会計（所管別未分解）',account+'特別会計','全国',
            '当初予算',a/1000,b/1000,['mof-special','mof-enacted'],account='特別会計',scope=sub,
            note='歳出の総計。会計間・勘定間の繰入等を含み、一般会計と足すと二重計上になる。個別府省への分解・地域帰属は未照合。')
    q1 = (RAW / 'q1.txt').read_text()
    source('mof-annual-2025','annual-2025.txt','2025年度一般会計決算概要',
           'https://www.mof.go.jp/policy/budget/budger_workflow/account/fy2025/ke080731.pdf',
           '歳出決算総額（支出済歳出額）：1,294,661億円、億円未満切捨て。',
           (RAW/'annual-2025.txt').read_text(),'2026-07-31')
    first = q1[:q1.index('\n以上の詳細は、別表第 1 のとおりである。')]
    source('mof-q1', 'q1.txt', '2026年度第1四半期予算使用の状況',
           'https://www.mof.go.jp/policy/budget/08_1a.pdf', '本文1頁、所管別内訳、単位：千円', first, '2026-09-01')
    names = [r['ministry'] for r in ROWS if r['basis'] == '当初予算' and r['account']=='一般会計']
    for line in first.splitlines():
        if not line.startswith('|'):
            continue
        for name in names:
            match = re.search(r'[\s|]*'.join(map(re.escape, name)) + r'(?=[\s|]*[\d－])', line)
            if not match:
                continue
            after = line[match.end():]
            amounts = re.findall(r'\d[\d,]*', after)
            if name == '防災庁' and '－' in after:
                continue  # no payments yet; do not turn missing into zero
            if len(amounts) >= 2:
                b, a = [int(n.replace(',', ''))/1000 for n in amounts[:2]]
                row(f'q1-{name}', name, '所管別支出済歳出額', '全国', '執行額（4〜6月）', a, b, ['mof-q1'],
                    period2025='2025年4〜6月', period2026='2026年4〜6月',
                    note='前年同期比較。繰越分などを含む。支出時期の違いがあるため、年間配分の減額を意味しない。')

    # The July cumulative column includes Q1; never add these two series.
    monthly={}; previous={}
    for year,date,slug in [(2025,'2025-09-19','0707'),(2026,'2026-09-18','0807')]:
        text=(RAW/f'monthly-{year}.txt').read_text()
        url=f'https://www.mof.go.jp/policy/budget/report/revenue_and_expenditure/fy{year}/{slug}b.html'
        source(f'mof-july-{year}',f'monthly-{year}.txt',f'{year}年度国庫歳入歳出状況：7月末歳出',url,
               '一般会計。支出済歳出額の「計」列（4〜7月累計）、千円未満切捨て。',text,date)
        source(f'mof-july-announcement-{year}',f'monthly-{year}-announcement.txt',f'{year}年度7月末状況の公表日',
               url.replace('b.html','a.html'),'公表日と対象月の説明。歳入額を歳出比較には使用しない。',published=date)
        monthly[year]={};previous[year]={}
        for line in text.splitlines():
            cells=[c.strip() for c in line.split('|')[1:-1]]
            if not cells or cells[0] not in names+['合計']:continue
            name=cells[0]
            if cells[4]=='-':continue
            monthly[year][name]=int(cells[4].replace(',',''))/1000
            previous[year][name]=int(cells[3].replace(',',''))/1000
        assert len(monthly[year])==19
    assert monthly[2025]['合計']==42254458.573 and monthly[2026]['合計']==45665915.744
    for name in sorted((set(monthly[2025])&set(monthly[2026]))-{'合計'}):
        q=next(r for r in ROWS if r['id']==f'q1-{name}')
        assert all(abs(previous[y][name]-q[f'amount{y}'])<0.002 for y in [2025,2026])
        row(f'july-{name}',name,'所管別支出済歳出額（7月末累計）','全国','執行額（4〜7月累計）',
            monthly[2025][name],monthly[2026][name],
            ['mof-july-2025','mof-july-2026','mof-july-announcement-2025','mof-july-announcement-2026'],
            period2025='2025年4〜7月',period2026='2026年4〜7月',
            note='同じ7月末までの累計を前年同期比較。4〜6月分を含むため四半期表と合算しない。支出時期・繰越等の影響を含み、地域への配分額や年間予算の増減ではない。')

    def grant(file, id, year):
        raw = (RAW/file).read_text()
        pos = raw.index('普通交付税 都道府県別決定額')
        source(id, file, f'{year}年度普通交付税大綱',
               f'https://www.soumu.go.jp/main_content/{"001022039" if year == 2025 else "001083398"}.pdf',
               '都道府県別決定額、道府県分と市町村分、単位：百万円', raw[pos:],
               '2025-07-29' if year == 2025 else '2026-07-24')
        result = {}
        for l in raw[pos:].splitlines():
            parts = [re.sub(r'\s', '', c) for c in l.split('|')[1:-1]]
            if len(parts) == 3 and parts[0] in SHORT:
                # A dash in Tokyo's prefectural allocation denotes non-recipient, not missing.
                result[SHORT[parts[0]]] = [0 if p == '-' else int(p.replace(',', '')) for p in parts[1:]]
        assert len(result) == 47
        return result
    a = grant('source-9-0.txt', 'lat-2025', 2025)
    b = grant('source-7-0.txt', 'lat-2026', 2026)
    for p in PREFS:
        for idx, scope in enumerate(['道府県分', '市町村分合計']):
            row(f'lat-{p}-{idx}', '総務省', '普通交付税', p, '交付決定', a[p][idx], b[p][idx],
                ['lat-2025', 'lat-2026'], scope=scope,
                note='当初算定同士。算定式に基づく一般財源。市町村分は都道府県内合計であり県自身への配分ではない。')

    source('aichi-explanation', 'source-11-4.txt', '愛知県：2026年度普通交付税等について',
           'https://www.pref.aichi.jp/soshiki/zaisei/2026kohuzei.html',
           '給与改定・物価高・教育無償化、地域未来基金費の説明',
           (RAW/'source-11-4.txt').read_text(), '2026-07-24')
    next(r for r in ROWS if r['id']=='lat-愛知県-0').update(
        explanation='県は給与改定・物価高・教育無償化等の算定要因を説明。地域未来基金費164億円も含む。個別の寄与額・税収変動は未分解。',
        explanation_status='公式説明あり・寄与未分解', explanation_source_ids=['aichi-explanation'])

    # MAFF filenames are NOT prefectural codes; identify actual names inside each table.
    agricultural = {}
    for year,date in [(2025,'2025-04-01'),(2026,'2026-04-07')]:
        source(f'maff-announcement-{year}',f'maff-{year}-announcement.txt',f'{year}年度農水省当初配分の発表',
               f'https://www.maff.go.jp/j/budget/kasyo/{year-2018}tousyo/index.html',
               '配分発表の入口。交付金は参考として都道府県別配分予定額を掲載。個別PDFの更新日時ではない。',published=date)
    for f in sorted(RAW.glob('maff-*.txt')):
        if not re.fullmatch(r'maff-\d{4}-\d+\.txt',f.name):continue
        year = int(f.name.split('-')[1]); t = f.read_text()
        id = f.stem; url = re.search(r'URL: (\S+)',t)[1]
        source(id, f.name, f'{year}年度農林水産公共事業：交付金配分予定額', url,
               '交付金表（国費、百万円）。配分予定額であり執行額ではない。公表日は当初配分発表の入口による。', t,
               '2025-04-01' if year==2025 else '2026-04-07')
        clean = re.sub(r'[|\s]+', ' ', t)
        for p in PREFS:
            for program in ['農山漁村地域整備交付金', '美しい森林づくり基盤整備交付金']:
                m = re.search(re.escape(p)+r'\s+'+program+r'\s+([\d,]+)', clean)
                if m:
                    key=(p,program); agricultural.setdefault(key,{})[year]=(int(m[1].replace(',','')),id)
    for (p,program), values in sorted(agricultural.items()):
        a, sa=values.get(2025,(None,None)); b,sb=values.get(2026,(None,None))
        row(f'maff-{p}-{program}', '農林水産省', program, p, '当初配分（予定国費）', a,b,
            [v for v in [sa,sb] if v]+[f'maff-announcement-{year}' for year in values], scope='都道府県分', precision='百万円に丸めた表示値',
            comparability='同範囲' if a is not None and b is not None else '片年度未取得',
            note='配分予定額。直轄・補助の事業費（地方負担を含む）と合算しない。未掲載や抽出不能はゼロとしない。')

    def care(file, year):
        t=(RAW/file).read_text(); start=t.index('|')
        id=f'care-{year}';url=f'https://www.mhlw.go.jp/content/{"12300000/001585303" if year==2025 else "001715704"}.pdf'
        source(id,file,f'{year}年度地域介護・福祉空間整備等施設整備交付金：一次協議',url,
               '都道府県分、計画数と'+('内示額' if year==2025 else '計画額')+'（千円）。指定都市・中核市は別枠。',t[start:],
               '2025-10-24' if year==2025 else '2026-06-26')
        source(f'care-announcement-{year}',f'care-{year}-announcement.txt',f'{year}年度一次協議内示の公式発表',
               f'https://www.mhlw.go.jp/stf/hard{year}-1_0000{3 if year==2025 else 1}.html',
               '対象PDFの発表ページ、公表日の根拠。',published='2025-10-24' if year==2025 else '2026-06-26')
        out={}
        for l in t[start:].splitlines():
            if not l.startswith('|'):continue
            cols=[c.strip() for c in l.split('|')[1:-1]]
            k=next((i for i,c in enumerate(cols) if c.isdigit()),None)
            if k is None or k+1>=len(cols):continue
            p=''.join(cols[:k]).replace(' ','')
            if p in PREFS:out[p]=(int(cols[k+1].replace(',',''))/1000,int(cols[k]))
        assert len(out)==47
        return out
    a=care('source-9-2.txt',2025);b=care('source-9-1.txt',2026)
    for p in PREFS:
        row(f'care-{p}','厚生労働省','地域介護・福祉空間整備等施設整備交付金',p,
            '一次協議内示（公表・更新時点差）',a[p][0],b[p][0],['care-2025','care-2026','care-announcement-2025','care-announcement-2026'],
            scope='都道府県分（指定都市・中核市を除く）',plans2025=a[p][1],plans2026=b[p][1],
            comparability='参考・更新時点差',
            note='2025年10月24日更新の内示額と2026年度一次協議の計画額を参考比較。2025原本は従前内示からの変更箇所を明記。2026原本は国土強靱化対策分を内数として掲載。金額列の名称と更新時点が異なり、当初予算額や執行額として扱わない。計画件数・採択段階・完了の確認が必要。')

    def defense(file,year):
        t=(RAW/file).read_text();id=f'defense-{year}'
        source(id,file,f'{year}年度特定防衛施設周辺整備調整交付金実施計画',
               f'https://www.mod.go.jp/j/approach/chouwa/hojokin/r{year-2018}-1/10.pdf',
               '市町村別金額、百万円、四捨五入。',t)
        out={};pref=None
        for line in t.splitlines():
            for p in PREFS:
                if p in line:pref=p;break
            clean=line.replace('|',' ').replace('）','） ').split('令和')[0]
            m=re.search(r'([一-龯ぁ-んァ-ヶー]+[市町村])\s+(\d+)\s*$',clean)
            if m and pref and '合' not in m[1]:
                city=m[1].replace('鎌ケ谷','鎌ヶ谷');out[(pref,city)]=int(m[2])
        return out
    a=defense('source-11-1.txt',2025);b=defense('source-11-0.txt',2026)
    for (p,city) in sorted(a.keys()|b.keys()):
        row(f'defense-{p}-{city}','防衛省','特定防衛施設周辺整備調整交付金',city,
            '実施計画',a.get((p,city)),b.get((p,city)),['defense-2025','defense-2026'],
            prefecture=p,scope='市町村分',precision='百万円に丸めた表示値',
            comparability='同範囲' if (p,city) in a and (p,city) in b else '片年度未取得',
            note='防衛施設周辺の実施計画額。新設・施設追加、算定要因、実施計画改定の確認が必要。国会議員の所在と交付金決定の因果は未検証。')

    source('mlit-inquiry', 'source-1.txt', '道路予算に係る週刊誌報道を受けた精査結果',
           'https://www.mlit.go.jp/road/content/002025983.pdf', '本文・別添、想定国費：百万円',
           (RAW/'source-1.txt').read_text()[230:], '2026-10-06')
    for municipality, amounts, ratios in [
        ('那須烏山市', [(142,105),(183,287),(325,392)], [-26.2,56.6,20.5]),
        ('那珂川町', [(49,23),(213,297),(262,319)], [-54.1,39.6,22.0])]:
        for i, scope in enumerate(['市町事業', '地域内の県事業', '市町＋地域内の県事業（合計）']):
            row(f'road-{municipality}-{i}', '国土交通省', '社会資本整備総合交付金（道路）', municipality,
                '当初配分（想定国費）', *amounts[i], ['mlit-inquiry'], prefecture='栃木県', scope=scope,
                published_change_pct=ratios[i], precision='百万円に丸めた表示値',
                note='要素事業の想定国費。国の決定は整備計画単位、要素事業への配分は自治体裁量。合計行と内訳行は重複する。')

    legislators = []
    housefiles = [RAW/'source-2.txt', *[RAW/f'house-{i}.txt' for i in range(2,11)]]
    for idx, f in enumerate(housefiles):
        t = f.read_text(); url = re.search(r'URL: (\S+)', t)[1]
        d = re.search(r'令和8年(\d+)月(\d+)日現在', t)
        asof = f'2026-{int(d[1]):02d}-{int(d[2]):02d}' if d else None
        # Preserve page-level dates: search caches differ, never overwrite with retrieval date.
        for m in re.finditer(r'([^\n|]+)君\s*\n([\s\S]*?)(?=[^\n|]+君|〒|\| ※|$)', t):
            name = m[1].strip().replace(' ', '')
            cells = [v.strip() for v in m[2].splitlines() if v.strip()]
            district = next((v for v in cells if re.fullmatch(r'(?:（比）.+|[^\d ]+\d+)', v)), None)
            caucus = cells[1] if len(cells)>1 and cells[1] not in [district] and not re.match(r'\d', cells[1]) else None
            proportional = bool(district and district.startswith('（比）'))
            short = re.sub(r'\d+$', '', district or '')
            prefs = [] if proportional else [SHORT[short]] if short in SHORT else []
            legislators.append(dict(id=f'house-{name}', name=name, reading=cells[0] if cells else None, chamber='衆議院', district=district,
                                    election_type='比例代表' if proportional else '小選挙区' if district else '未取得', prefectures=prefs,
                                    caucus=caucus, party=None, party_source=None, as_of=asof, source_url=url))
    t = (RAW/'source-3.txt').read_text()
    for l in t.splitlines():
        cols = [c.strip() for c in l.split('|')[1:-1]]
        if len(cols) < 5 or not cols[4].startswith('令和'):
            continue
        proportional = cols[3] == '比例'
        district = '全国比例' if proportional else cols[0]
        name = (cols[1].split('[')[0]).replace(' ', '')
        short = district.replace('県', '')
        prefs = [SHORT.get(p,p) for p in short.split('・')] if not proportional else []
        prefs = [p for p in prefs if p in PREFS]
        legislators.append(dict(id=f'senate-{name}', name=name, reading=cols[2], chamber='参議院', district=district,
                                election_type='比例代表' if proportional else '選挙区', prefectures=prefs,
                                caucus=cols[0] if proportional else cols[3], party=None, party_source=None,
                                as_of='2026-09-04', source_url='https://www.sangiin.go.jp/japanese/giin/hireiku/hireiku.htm'))
    # Explicit party column in a prefectural government source, no caucus-to-party inference.
    party_source = 'https://www.pref.tochigi.lg.jp/a51/documents/20260413152401.pdf'
    source('tochigi-party','tochigi-party.txt','栃木県関係国会議員一覧',party_source,
           '所属政党欄。住所・電話番号は保存しない。',(RAW/'tochigi-party.txt').read_text(),'2026-04-01')
    verified={}
    for line in (RAW/'tochigi-party.txt').read_text().splitlines():
        match=re.match(r'^(.+?)[（(].*[）)]\s+.+\s+(自由民主党|日本維新の会|無所属)$',line)
        if match:verified[match[1].replace(' ','')]=match[2]
    assert len(verified)==9
    for r in legislators:
        if r['name'] in verified:
            r.update(party=verified[r['name']], party_source=party_source, party_as_of='2026-04-01')
            if r['election_type']=='比例代表':
                r['related_prefectures']=['栃木県']

    party_roster_stats=apply_party_rosters(legislators,RAW,source,EXTRACT_DATE)
    party_count=sum(m['party'] is not None for m in legislators)
    party_conflicts=sum(m['party_status']=='資料間不一致' for m in legislators)

    # Reviewed source-backed additions and receipts are committed, not inferred
    # from whatever happens to be present in a local cache.
    mlit=json.loads((ROOT/'data/reviewed-mlit.json').read_text())
    SOURCES.extend(mlit['sources']);ROWS.extend(mlit['rows'])
    structured=json.loads((ROOT/'data/mof-structured-verification.json').read_text())
    SOURCES.extend(structured['sources'])
    for r in ROWS:
        if r['region']=='全国' and r['basis']=='当初予算':
            category='general' if r['account']=='一般会計' else 'special'
            r['source_ids'].extend(f'mof-csv-{category}-{year}' for year in (2025,2026))
    original_coverage=apply_originals(ROOT,SOURCES,ROWS)
    for s in SOURCES:
        if s['id']=='care-2026':
            s['published_verification']='6月26日の公式発表頁と掲載PDFは原本確認済み。収載PDFとはURLが異なり47金額・計画数は一致するが、収載PDF自体の版・公表日は未確定。'
        elif s['id']=='mlit-inquiry':
            s['published_verification']='検索抽出時の記録。公表日の掲載頁原本照合は未実施。'

    agencies = {
        '内閣':['内閣官房','内閣法制局','人事院'],
        '内閣府':['公正取引委員会','国家公安委員会','警察庁','個人情報保護委員会','金融庁','消費者庁','こども家庭庁'],
        '総務省':['公害等調整委員会','消防庁'], '法務省':['出入国在留管理庁','公安調査庁'],
        '財務省':['国税庁'], '文部科学省':['スポーツ庁','文化庁'],
        '厚生労働省':['中央労働委員会'], '農林水産省':['林野庁','水産庁'],
        '経済産業省':['資源エネルギー庁','特許庁','中小企業庁'],
        '国土交通省':['観光庁','気象庁','運輸安全委員会','海上保安庁'],
        '環境省':['原子力規制委員会'], '防衛省':['防衛装備庁']}
    coverage=[]
    for name in [r['ministry'] for r in ROWS if r['basis']=='当初予算' and r['account']=='一般会計']+['復興庁']:
        regional=[r for r in ROWS if r['ministry']==name and r['region']!='全国']
        coverage.append(dict(name=name,parent=None,national=name!='復興庁',regional_rows=len(regional),
                             status='一部制度を収載・地域全体は未完了' if regional else '地域別・事業別は未収載',
                             note='復興庁は特別会計のため一般会計表に含まれず。' if name=='復興庁' else '所管総額と個別制度の配分は別の調査段階。'))
        for child in agencies.get(name,[]):
            coverage.append(dict(name=child,parent=name,national=False,regional_rows=0,
                                 status='母省の総額に含む・単独比較未実施',note='所属機関ごとの事業・地域配分を別途照合する必要がある。'))
    snapshot = dict(schema_version=1, as_of=DATE, prefectures=PREFS, rows=ROWS, coverage=coverage,
                    annual2025=dict(spent_million_yen=129466100,source_id='mof-annual-2025',
                                    note='2025年度年間の決算概要。2026年度は年度未終了のため年間決算は存在しない。前年同期の四半期・月末累計執行額とは別扱い。'),
                    sources=SOURCES, legislators=legislators,party_roster_stats=party_roster_stats,
                    original_coverage=original_coverage,
                    party_coverage=dict(verified=party_count,conflicts=party_conflicts,total=len(legislators)),
                    limitations=[
                        '全府省庁の地域別・事業別配分と執行額の網羅調査は継続中。所管総額の確認を地域調査完了とは扱わない。',
                        '一般会計19所管、特別会計14会計34勘定等、第1四半期・7月末累計支出、一部制度の地域配分を収載。道路の都道府県・地方整備局別事業費を追加。国費と事業費を合算しない。特別会計の府省別・地域別分解は未収載。',
                        f'原本数値を両年度照合した比較行は{original_coverage["fully_verified_comparison_rows"]}件。原本未掲載の値は欠損のまま残す。抽出本文と原本ファイルのハッシュは別に保存。議員名簿の統一時点での原本照合は未完了。',
                        f'所属党は{party_count}人を一次資料で照合。{party_conflicts}人は資料間不一致。未照合を会派から推定しない。党の一覧は資料日未確認で、取得日を所属の基準日とは扱わない。',
                        '現在の名簿と2025/2026年度の配分決定時点の議員は一致しない。政治的因果関係の検証には当時の名簿が必要。'])
    (ROOT/'public'/'data.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2)+'\n')
    print(f'{len(ROWS)} comparison rows, {len(legislators)} legislators, {len(SOURCES)} sources')


if __name__ == '__main__':
    main()
