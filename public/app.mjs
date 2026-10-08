import { change, screen, membersFor, safeURL, csvCell } from './analysis.mjs';

const $ = id => document.getElementById(id);
const number = new Intl.NumberFormat('ja-JP', { maximumFractionDigits: 3 });
let data, view = 'regional', page = 0, visible = [];
const pageSize = 20;
const el = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
const amount = n => n === null ? '未取得' : number.format(n);
const link = (label, url) => {
  const u = safeURL(url); if (!u) return el('span', label);
  const a = el('a', label); a.href = u; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a;
};
const options = (id, values, empty) => {
  const select = $(id), selected = select.value;
  select.replaceChildren(); if (empty !== undefined) { const o = el('option', empty); o.value = ''; select.append(o); }
  for (const v of values) { const o = el('option', v); o.value = v; select.append(o); }
  if ([...select.options].some(o => o.value === selected)) select.value = selected;
};
function baseRows() {
  return data.rows.filter(r => view === 'national' ? r.basis === '当初予算' && r.account === '一般会計' :
    view === 'special' ? r.account === '特別会計' :
    view === 'execution' ? r.basis.startsWith('執行額') : view === 'reference' ? r.comparability.startsWith('参考') :
    r.region !== '全国' && !r.comparability.startsWith('参考'));
}
function thresholds() {
  const bounded = (id, fallback, max) => { const n = Number($(id).value); return $(id).value === '' || !Number.isFinite(n) ? fallback : Math.min(max, Math.max(0, n)); };
  return { pct: bounded('pct', 30, 1000), amount: bounded('amount', 10, 1e8), gap: bounded('gap', 20, 1000) };
}
function rowMembers(r) { return membersFor(r.prefecture, data.legislators, true); }
function partyLabel(m) { return m.party ?? (m.party_status==='資料間不一致' ? `資料間不一致（${[...new Set(m.party_evidence.map(e=>e.party))].join('／')}）` : '未照合'); }
function memberLine(m) { return `${m.name}（${m.district ?? '選挙区未取得'}） / 党：${partyLabel(m)} / 会派：${m.caucus ?? '未取得'}`; }
function render() {
  $('data-view').hidden = view === 'coverage'; $('coverage-view').hidden = view !== 'coverage';
  document.querySelectorAll('[data-view]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.view === view)));
  if (view === 'coverage') { renderCoverage(); return; }
  const base = baseRows();
  const regional = base.some(r => r.region !== '全国');
  $('prefecture').disabled = !regional;
  options('ministry', [...new Set(base.map(r => r.ministry))], 'すべて');
  options('program', [...new Set(base.filter(r => !$('ministry').value || r.ministry === $('ministry').value).map(r => r.program))], 'すべて');
  const q = $('query').value.trim().toLocaleLowerCase('ja-JP');
  let scoped = base.filter(r => (!regional || !$('prefecture').value || r.prefecture === $('prefecture').value) &&
    (!$('ministry').value || r.ministry === $('ministry').value) && (!$('program').value || r.program === $('program').value) &&
    (!q || [r.region,r.ministry,r.program,r.scope,...rowMembers(r).map(memberLine)].join(' ').toLocaleLowerCase('ja-JP').includes(q)));
  const t = thresholds();
  scoped = scoped.map(r => ({ ...r, analysis: screen(r, data.rows, t) }));
  const candidates = scoped.filter(r => r.analysis.candidate);
  $('row-count').textContent = String(scoped.length);
  $('candidate-count').textContent = String(candidates.length);
  $('incomplete-count').textContent = String(scoped.filter(r => r.comparability !== '同範囲').length);
  visible = $('candidate-only').checked ? candidates : scoped;
  const key = $('sort').value;
  visible.sort((a,b) => key === 'region' ? (a.prefecture ?? a.region).localeCompare(b.prefecture ?? b.region, 'ja') :
    (key === 'delta' ? Math.abs(b.analysis.delta ?? -1) - Math.abs(a.analysis.delta ?? -1) :
      (b.analysis.pct === null ? -1 : Math.abs(b.analysis.pct)) - (a.analysis.pct === null ? -1 : Math.abs(a.analysis.pct))));
  page = Math.min(page, Math.max(0, Math.ceil(visible.length / pageSize)-1));
  const notes = {
    regional: '地域配分の収載制度は一部です。同制度の合計増減率は、比較可能な収載行を金額で加重して計算します。全国予算全体の増減率ではありません。合計・内訳の重複行は合算しません。',
    national: '一般会計の成立当初予算を比較。2025年度は修正成立後、2026年度は政府案どおり成立。所管総額には外局などを含み、地域の配分を直接示す値ではありません。特別会計は別表示。',
    special: '14特別会計の34勘定等を当初予算の歳出欄で比較。2025年度は括弧内の当初額を使用。繰入・国債償還などを含む総計で、一般会計や他の勘定と足すと二重計上になります。府省別・地域別の分解は未照合。',
    execution: '両年度の4〜6月の支出済歳出額を比較。2026年度第2四半期の表は今回確認した公表資料にありません。年間決算と四半期の執行額は比較しません。防災庁のダッシュはゼロとして収載していません。',
    reference: '比較条件の異なる参考表です。2025補正後と2026当初、または公表時点の違う一次協議内示を並べています。これらを減額の確認候補の自動判定には使いません。'
  };
  $('view-note').textContent = notes[view]; $('context').hidden = view !== 'regional';
  $('annual-note').hidden = view !== 'execution';
  const annualSource=data.sources.find(s=>s.id===data.annual2025.source_id);
  $('annual-note').replaceChildren(document.createTextNode(`年間決算（別枠）：2025年度一般会計の支出済歳出額は129兆4,661億円。${data.annual2025.note} `),link('決算概要の出典 ↗',annualSource.url));
  renderChart(); renderRows();
}
function renderChart() {
  const chart = $('chart'); chart.replaceChildren();
  const rows = visible.filter(r => r.analysis.pct !== null).sort((a,b)=>Math.abs(b.analysis.pct)-Math.abs(a.analysis.pct)).slice(0,10);
  $('chart-title').textContent = '表示条件での増減率の大きさ（上位10件）';
  if (!rows.length) { chart.append(el('p','表示できる比較がありません。未取得・前年ゼロは増減率を計算しません。','muted')); return; }
  const max = Math.max(1,...rows.map(r => Math.abs(r.analysis.pct)));
  for (const r of rows) {
    const line = el('div',undefined,'bar-row'); const label = el('button',undefined,'bar-label');
    label.append(el('strong',r.region),el('small',r.program),el('small',`${r.scope ?? r.ministry} / ${amount(r.amount2025)} → ${amount(r.amount2026)} 百万円`));
    label.type = 'button'; label.title = `${r.program} / ${r.scope ?? ''}`; label.onclick = () => showDetail(r);
    const track = el('div',undefined,`bar-track ${r.analysis.pct < 0 ? 'negative' : ''}`);
    const bar = el('progress'); bar.max = max; bar.value = Math.abs(r.analysis.pct);
    bar.setAttribute('aria-label',`${r.region} ${r.analysis.label}`); track.append(bar);
    line.append(label,track,el('span',r.analysis.label,`number ${r.analysis.pct < 0 ? 'decrease' : 'increase'}`)); chart.append(line);
  }
}
function renderRows() {
  const body = $('rows'); body.replaceChildren();
  for (const r of visible.slice(page*pageSize,(page+1)*pageSize)) {
    const tr = el('tr'), identity = el('td');
    identity.append(el('strong',r.region),el('small',r.ministry),el('div',r.program),el('small',r.scope ?? r.basis));
    const delta = el('td',r.analysis.label,`number ${r.analysis.pct < 0 ? 'decrease' : 'increase'}`);
    delta.append(el('small',r.analysis.delta === null ? '' : `${r.analysis.delta >= 0 ? '+' : ''}${amount(r.analysis.delta)} 百万円`));
    const status = el('td'); status.append(el('span',r.analysis.candidate ? '確認候補' : r.comparability === '同範囲' ? 'しきい値未満' : '比較条件を確認',`badge ${r.analysis.candidate ? 'candidate' : ''}`));
    if (r.explanation) status.append(el('small',r.explanation_status));
    if (r.analysis.peer !== null) status.append(el('small',`同制度合計：${r.analysis.peer.toFixed(1)}%`));
    const politicians = el('td'); const all = rowMembers(r);
    if (!all.length) politicians.append(el('small',r.region === '全国' ? '全国総額（地域への割当なし）' : '地域の議員情報を未取得'));
    for (const chamber of ['衆議院','参議院']) {
      const ms = all.filter(m => m.chamber === chamber);
      if (ms.length) {
        politicians.append(el('strong',chamber));
        for (const m of ms.slice(0,3)) politicians.append(el('small',memberLine(m)));
        if (ms.length>3) politicians.append(el('small',`ほか${ms.length-3}人（詳細に全員表示）`));
      }
    }
    const more = el('td'), b = el('button','出典・議員'); b.type='button';b.onclick=()=>showDetail(r);more.append(b);
    tr.append(identity,el('td',amount(r.amount2025),'number'),el('td',amount(r.amount2026),'number'),delta,status,politicians,more);body.append(tr);
  }
  if (!visible.length) { const tr=el('tr'), td=el('td','該当する収載データはありません。未収載は予算ゼロを意味しません。');td.colSpan=7;tr.append(td);body.append(tr); }
  $('prev').disabled = page===0; $('next').disabled = (page+1)*pageSize>=visible.length;
  $('page-status').textContent = `${visible.length ? page*pageSize+1 : 0}〜${Math.min((page+1)*pageSize,visible.length)} / ${visible.length}件`;
}
function sourceBlock(id) {
  const s = data.sources.find(v=>v.id===id); const block=el('section');
  if (!s) { block.append(el('p','出典未取得'));return block; }
  block.append(link(s.title+' ↗',s.url),el('p',`${s.locator} / 公表日：${s.published ?? '未確認'} / 本文取得：${s.accessed}`,'muted'));
  const detail=el('details');detail.append(el('summary','確認した本文・抽出テキストのハッシュ'),el('pre',s.excerpt),el('small',`SHA-256（原本ファイルのハッシュではありません）：${s.sha256_extracted_text}`));block.append(detail);return block;
}
function showDetail(row) {
  const r={...row,analysis:screen(row,data.rows,thresholds())}, content=$('detail-content');content.replaceChildren();
  content.append(el('h2',`${r.region} / ${r.program}`),el('p',`${r.ministry}・${r.account}・${r.basis}・${r.scope ?? ''}`),
    el('p',`${r.period2025}：${amount(r.amount2025)} → ${r.period2026}：${amount(r.amount2026)} 百万円 / ${r.analysis.label}`),
    el('p',r.note),el('p',`比較条件：${r.comparability} / 精度：${r.precision ?? '出典単位から換算'}`,'muted'));
  if (r.published_change_pct !== undefined) content.append(el('p','増減率は国交省の公表値。表示金額が丸められているため、表示金額からの計算と端数が異なります。','muted'));
  if (r.plans2025 !== undefined) content.append(el('p',`計画件数：${r.plans2025} → ${r.plans2026} 件`));
  content.append(el('h3','次に確認すること'),el('p',r.analysis.reasons.join(' / ')||'設定したしきい値に達していません。'));
  const ul=el('ul');for(const t of ['申請・要望額に対する配分率、採択基準と制度変更','完了・新規事業・工事進捗、災害復旧、人口や税収などの需要','配分決定時点の議員・首長、問い合わせ記録と決定過程']) ul.append(el('li',t));content.append(ul);
  if (r.explanation) { content.append(el('h3','公式の説明'),el('p',r.explanation));for(const id of r.explanation_source_ids)content.append(sourceBlock(id)); }
  content.append(el('h3','金額の出典'));for(const id of r.source_ids)content.append(sourceBlock(id));
  content.append(el('h3','地域の国会議員（公表時点）'));
  for(const m of rowMembers(r)) content.append(memberCard(m));
  if (!rowMembers(r).length) content.append(el('p','地域への割当なし、または議員情報が未取得。'));
  $('detail').showModal();
}
function memberCard(m) {
  const card=el('article',undefined,'member');card.append(el('h3',m.name),el('p',`${m.chamber}・${m.election_type}・${m.district ?? '選挙区未取得'}`),
    el('p',`所属党：${partyLabel(m)}`),el('p',`会派：${m.caucus ?? '未取得'}`),el('small',`名簿基準日：${m.as_of ?? '未確認'}`),link('国会名簿 ↗',m.source_url));
  for(const e of m.party_evidence??[]) {
    card.append(link(`${e.party}の確認資料 ↗`,e.url),el('small',`所属資料の基準日：${e.as_of??'未確認'} / 照合日：${e.checked_at}`));
    if(e.roster_name)card.append(el('small',`党の表記：${e.roster_name}`));
  }
  return card;
}
function renderMembers() {
  const p=$('member-pref').value, includeProportional=$('proportional').checked;
  const members=p ? membersFor(p,data.legislators,includeProportional) : data.legislators.filter(m=>includeProportional || m.election_type!=='比例代表');
  $('party-coverage').textContent=`全国の所属党照合：${data.party_coverage.verified} / ${data.party_coverage.total}人。資料間不一致：${data.party_coverage.conflicts}人。党の資料日が不明な場合は取得日を基準日に読み替えません。`;
  $('members').replaceChildren(...members.map(memberCard));
  if(!members.length)$('members').append(el('p','この地域に対応する議員を未取得。'));
}
function renderCoverage() {
  $('coverage').replaceChildren(...data.coverage.map(c=>{
    const row=el('div',undefined,'coverage-item'),text=el('div');
    text.append(el('div',c.status),el('small',`${c.national?'一般会計所管総額を比較済み / ':''}地域配分：${c.regional_rows}レコード${c.parent?` / 母省：${c.parent}`:''}`),el('small',c.note));row.append(el('strong',c.name),text);return row;
  }));
}
function exportCSV() {
  const fields=['region','prefecture','ministry','account','program','scope','basis','period2025','period2026','amount2025','amount2026','unit','comparability'];
  const lines=[fields.concat(['change_pct','candidate','screen_reasons','legislators','source_urls','legislator_evidence']).map(csvCell).join(',')];
  for(const r of visible)lines.push(fields.map(f=>csvCell(r[f])).concat([
    csvCell(r.analysis.pct),csvCell(r.analysis.candidate),csvCell(r.analysis.reasons.join(' / ')),
    csvCell(rowMembers(r).map(memberLine).join(' / ')),csvCell([...r.source_ids,...r.explanation_source_ids??[]].map(id=>data.sources.find(s=>s.id===id)?.url).join(' ')),
    csvCell(JSON.stringify(rowMembers(r).map(m=>({name:m.name,roster_as_of:m.as_of,roster_url:m.source_url,party_status:m.party_status,party_evidence:m.party_evidence}))))]).join(','));
  const blob=new Blob(['\uFEFF',lines.join('\r\n')],{type:'text/csv;charset=utf-8'}),u=URL.createObjectURL(blob),a=el('a');a.href=u;a.download=`budget-${view}-${data.as_of}.csv`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),1000);
}
async function start() {
  try {
    const response=await fetch('./data.json',{credentials:'omit'});if(!response.ok)throw new Error(`HTTP ${response.status}`);
    data=await response.json();if(data.schema_version!==1||!Array.isArray(data.rows))throw new Error('データ形式が一致しません');
    $('date').textContent=`/ 調査日 ${data.as_of}`;
    options('prefecture',data.prefectures,'全国の地域');options('member-pref',data.prefectures,'全国の議員');$('member-pref').value='栃木県';
    $('limits-list').replaceChildren(...data.limitations.map(t=>el('li',t)));
    const parties=data.legislators.filter(m=>m.party!==null).length;
    $('coverage-summary').textContent=`収載：${data.rows.length}比較レコード、議員名簿${data.legislators.length}人（衆議院464人、参議院247人の抽出本文）。選挙区未取得23人。所属党の一次資料照合：${parties}人。`;
    $('source-count').textContent=`公式出典 ${data.sources.length}件。`;
    for(const id of ['prefecture','ministry','program','query','pct','amount','gap','candidate-only','sort'])$(id).addEventListener(id==='query'?'input':'change',()=>{page=0;render();});
    document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{view=b.dataset.view;page=0;$('candidate-only').checked=false;render();});
    $('prev').onclick=()=>{page--;renderRows();};$('next').onclick=()=>{page++;renderRows();};
    $('export').onclick=exportCSV;$('close-detail').onclick=()=>$('detail').close();
    $('limitations-toggle').onclick=()=>{$('limitations').open=!$('limitations').open;};
    $('reset').onclick=()=>{for(const id of ['prefecture','ministry','program','query'])$(id).value='';$('pct').value=30;$('amount').value=10;$('gap').value=20;$('candidate-only').checked=false;page=0;render();};
    $('member-pref').onchange=renderMembers;$('proportional').onchange=renderMembers;
    render();renderMembers();
  }catch(error){$('error').hidden=false;$('error').textContent=`データを読み込めませんでした：${error.message}。READMEの手順でHTTPサーバーを起動してください。`;}
}
start();
