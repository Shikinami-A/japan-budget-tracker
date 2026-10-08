import { change, screen, membersFor, membersForRow, safeURL, csvCell } from './analysis.mjs';

const $ = id => document.getElementById(id);
const number = new Intl.NumberFormat('ja-JP', { maximumFractionDigits: 3 });
let data, view = 'regional', page = 0, visible = [];
const pageSize = 20;
const el = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
const amount = n => n === null ? '未取得' : number.format(n);
const rowAmount = (r,year) => r[`amount${year}`]===null ?
  (r[`amount_status${year}`] ?? (r.comparability==='片年度非掲載' ? '非掲載' : '未取得')) : amount(r[`amount${year}`]);
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
  return data.rows.filter(r => view === 'programs' || view === 'institutions' ? r.view_group === view :
    view === 'national' ? r.basis === '当初予算' && r.account === '一般会計' :
    view === 'special' ? r.account === '特別会計' :
    view === 'execution' ? r.basis.startsWith('執行額') : view === 'reference' ?
    r.view_group==='reference' || !r.view_group && r.comparability.startsWith('参考') :
    r.region !== '全国' && !r.view_group && !r.comparability.startsWith('参考'));
}
function thresholds() {
  const bounded = (id, fallback, max) => { const n = Number($(id).value); return $(id).value === '' || !Number.isFinite(n) ? fallback : Math.min(max, Math.max(0, n)); };
  return { pct: bounded('pct', 30, 1000), amount: bounded('amount', 10, 1e8), gap: bounded('gap', 20, 1000) };
}
function rowMembers(r) { return membersForRow(r, data.legislators, true); }
function rowSourceIDs(r) {
  return [...new Set([...r.source_ids,...r.explanation_source_ids??[],
    ...r.driver_checks?.flatMap(c=>c.source_ids)??[],
    ...r.municipality_mapping ? [r.municipality_mapping.source_id,r.municipality_mapping.boundary_source_id].filter(Boolean) : []])];
}
function partyLabel(m) { return m.party ?? (m.party_status==='資料間不一致' ? `資料間不一致（${[...new Set(m.party_evidence.map(e=>e.party))].join('／')}）` : '未照合'); }
function memberLine(m) { return `${m.name}（${m.district ?? '選挙区未取得'}） / 党：${partyLabel(m)} / 会派：${m.caucus ?? '未取得'}`; }
function render() {
  $('data-view').hidden = view === 'coverage'; $('coverage-view').hidden = view !== 'coverage';
  document.querySelectorAll('[data-view]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.view === view)));
  if (view === 'coverage') { renderCoverage(); return; }
  const base = baseRows();
  const regional = base.some(r => r.prefecture !== null);
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
    regional: '地域配分の収載制度は一部です。道路補助・直轄の事業費は地方負担を含み、交付金の国費と合算しません。同制度の合計増減率は比較可能な収載行を金額で加重して計算します。全国予算全体の増減率ではありません。合計・内訳の重複行は合算しません。',
    national: '一般会計の成立当初予算を比較。2025年度は修正成立後、2026年度は政府案どおり成立。所管総額には外局などを含み、地域の配分を直接示す値ではありません。特別会計は別表示。',
    programs: '一般会計全19所管の859項を、所管・組織・項名で結合した全国内訳です。同名称でも制度の連続性は未確認のため参考比較とし、自動判定に使いません。名称変更や移管を新設・廃止と認定せず、片年度非掲載はゼロにしません。親の所管総額と合算しません。',
    institutions: '国立大学法人等の当初予算積算内訳86区分。交付決定額・決算ではありません。複数キャンパスの地域帰属を確認していないため、大学名や本部所在地から県や議員を割り当てません。全国所管総額・事業内訳と重複するため合算しません。',
    special: '14特別会計の34勘定等を当初予算の歳出欄で比較。2025年度は括弧内の当初額を使用。繰入・国債償還などを含む総計で、一般会計や他の勘定と足すと二重計上になります。府省別・地域別の分解は未照合。',
    execution: '4〜6月と4〜7月累計を、それぞれ前年の同期間と比較します。7月末累計には第1四半期分が含まれるため合算しません。制度の絞り込みで期間を選べます。2026年度第2四半期は未収載。年間決算との比較・防災庁のダッシュのゼロ化は行いません。',
    reference: '比較条件の異なる参考表です。補正後対当初、厚労省の一次協議、環境省の会計区分が異なる4月内示、こども家庭庁の第1次内示を収載。対象・財源・通知段階や年度全体の確認が必要です。片年度非掲載も同じ制度内に表示し、自動判定には使いません。'
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
    status.append(el('small',r.evidence_status));
    if (r.explanation) status.append(el('small',r.explanation_status));
    if (r.analysis.peer !== null) status.append(el('small',`同制度合計：${r.analysis.peer.toFixed(1)}%`));
    const politicians = el('td'); const all = rowMembers(r);
    politicians.append(el('small',r.legislator_mapping_status ?? '地域対応未確認'));
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
    tr.append(identity,el('td',rowAmount(r,2025),'number'),el('td',rowAmount(r,2026),'number'),delta,status,politicians,more);body.append(tr);
  }
  if (!visible.length) { const tr=el('tr'), td=el('td','該当する収載データはありません。未収載は予算ゼロを意味しません。');td.colSpan=7;tr.append(td);body.append(tr); }
  $('prev').disabled = page===0; $('next').disabled = (page+1)*pageSize>=visible.length;
  $('page-status').textContent = `${visible.length ? page*pageSize+1 : 0}〜${Math.min((page+1)*pageSize,visible.length)} / ${visible.length}件`;
}
function sourceBlock(id) {
  const s = data.sources.find(v=>v.id===id); const block=el('section');
  if (!s) { block.append(el('p','出典未取得'));return block; }
  block.append(link(s.title+' ↗',s.url),el('p',`${s.locator} / 公表日：${s.published ?? '未確認'} / 本文取得：${s.accessed} / ${s.retrieved_via}`,'muted'));
  if(s.published_verification)block.append(el('small',`公表日の確認範囲：${s.published_verification}`));
  const detail=el('details');detail.append(el('summary','確認した本文・抽出テキストのハッシュ'),el('pre',s.excerpt),el('small',`SHA-256（原本ファイルのハッシュではありません）：${s.sha256_extracted_text}`));block.append(detail);
  if (s.original) {
    const o=s.original, receipt=el('details');receipt.className='original-receipt';
    receipt.append(el('summary','原本の取得・照合記録'),el('p',`取得日時（UTC）：${o.retrieved_at_utc} / ${number.format(o.bytes)} bytes / ${o.content_type}`),el('small',`SHA-256（原本ファイル）：${o.sha256_original}`));
    for(const v of o.verifications)receipt.append(el('p',`${v.status==='matched'?'指定範囲を照合済み':'取得済み・数値照合なし'} / ${v.locator} / 照合日：${v.checked_at}`),el('small',v.method));
    block.append(receipt);
  } else block.append(el('small','原本取得・照合記録は未収載。'));
  return block;
}
function showDetail(row) {
  const r={...row,analysis:screen(row,data.rows,thresholds())}, content=$('detail-content');content.replaceChildren();
  content.append(el('h2',`${r.region} / ${r.program}`),el('p',`${r.ministry}・${r.account}・${r.basis}・${r.scope ?? ''}`),
    el('p',`${r.period2025}：${rowAmount(r,2025)} → ${r.period2026}：${rowAmount(r,2026)} 百万円 / ${r.analysis.label}`),
    el('p',r.note),el('p',`比較条件：${r.comparability} / ${r.evidence_status} / 精度：${r.precision ?? '出典単位から換算'}`,'muted'));
  if (r.published_change_pct !== undefined) content.append(el('p','増減率は国交省の公表値。表示金額が丸められているため、表示金額からの計算と端数が異なります。','muted'));
  if (r.plans2025 !== undefined) content.append(el('p',`計画件数：${r.plans2025} → ${r.plans2026} 件`));
  content.append(el('h3','次に確認すること'),el('p',r.analysis.reasons.join(' / ')||'設定したしきい値に達していません。'));
  const ul=el('ul');for(const t of ['申請・要望額に対する配分率、採択基準と制度変更','完了・新規事業・工事進捗、災害復旧、人口や税収などの需要','配分決定時点の議員・首長、問い合わせ記録と決定過程']) ul.append(el('li',t));content.append(ul);
  if (r.explanation) { content.append(el('h3','公式の説明'),el('p',r.explanation));for(const id of r.explanation_source_ids)content.append(sourceBlock(id)); }
  if (r.driver_checks?.length) {
    content.append(el('h3','増減理由と決定過程の確認状態'));
    for (const check of r.driver_checks) {
      content.append(el('p',`${check.dimension}：${check.status}`),el('small',check.finding));
    }
    for (const id of [...new Set(r.driver_checks.flatMap(c=>c.source_ids))]) content.append(sourceBlock(id));
  }
  content.append(el('h3','金額の出典'));for(const id of r.source_ids)content.append(sourceBlock(id));
  content.append(el('h3','地域の国会議員（公表時点）'));
  content.append(el('p',`${r.legislator_mapping_status ?? '地域対応未確認'}。配分決定時点の議員・党籍は未確認。`,'muted'));
  if (r.municipality_mapping) {
    const mapping=r.municipality_mapping;
    content.append(el('p',`区割りの資料基準日：${mapping.as_of ?? '未確認'} / 対応する小選挙区：${mapping.districts.join('・')}${mapping.boundary_detail ? ' / '+mapping.boundary_detail.map(b=>b.district+'：'+b.area).join('・') : ''}`));
    content.append(sourceBlock(mapping.source_id));
    if(mapping.boundary_source_id)content.append(sourceBlock(mapping.boundary_source_id));
  }
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
    if(e.profile_url) {
      card.append(link('党の個別プロフィール原本 ↗',e.profile_url));
      const original=(data.party_originals ?? []).find(o=>o.source_id===e.supporting_original_id);
      if(original)card.append(el('small',`原本取得（UTC）：${original.retrieved_at_utc} / SHA-256：${original.sha256_original}`));
    }
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
function showObservation(observation) {
  const content=$('detail-content');content.replaceChildren();
  content.append(el('h2',`${observation.recipient} / 第${observation.notification_round}回`),
    el('p',observation.program),el('p',`通知日：${observation.published} / 年度：${observation.fiscal_year ?? '未確認'} / ${observation.fiscal_year_status}`),
    el('p',`${observation.basis} / 国費：${amount(observation.national_cost_million_yen)} / 事業費：${amount(observation.business_cost_million_yen)} 百万円`),
    el('p',`${observation.numeric_verification} / ${observation.comparison_status}`),
    el('p','通知回の観測値です。年間合計・支出済額・確定交付額として合算せず、年度の異なる原本とは比較しません。市町村と選挙区、配分決定時点の議員・党籍は未照合。'));
  if(observation.internal_consistency_status)content.append(el('p',`原本内部の整合性：${observation.internal_consistency_status}`));
  for(const id of observation.source_ids) content.append(sourceBlock(id));
  $('detail').showModal();
}
function observationTable(observations) {
  const detail=el('details');detail.append(el('summary',`年度比較に採用していない通知の原表：${observations.length}観測`));
  detail.append(el('p','通知回ごとに数値を照合。年度欄未確認は通知日から推定せず、暫定分・成立後通知・再掲事業費を合算しません。'));
  const wrap=el('div',undefined,'table-wrap'),table=el('table'),head=el('thead'),header=el('tr');
  for(const title of ['通知日・回／年度確認','事業・主体','国費／事業費（百万円）','数値・比較条件','原本'])header.append(el('th',title));
  head.append(header);table.append(head);const body=el('tbody');
  for(const o of observations) {
    const row=el('tr'),more=el('td'),button=el('button','原表・条件');button.type='button';button.onclick=()=>showObservation(o);more.append(button);
    row.append(el('td',`${o.published} 第${o.notification_round}回 / 年度${o.fiscal_year ?? '未確認'}`),
      el('td',`${o.recipient} / ${o.program}`),el('td',`${amount(o.national_cost_million_yen)} / ${amount(o.business_cost_million_yen)}`),
      el('td',`${o.numeric_verification} / ${o.comparison_status}${o.internal_consistency_status ? ' / '+o.internal_consistency_status : ''}`),more);body.append(row);
  }
  table.append(body);wrap.append(table);detail.append(wrap);return detail;
}
function renderCoverage() {
  $('coverage').replaceChildren(...data.coverage.map(c=>{
    const row=el('div',undefined,'coverage-item'),text=el('div');
    text.append(el('div',c.status),el('small',`${c.national?'一般会計所管総額を比較済み / ':''}組織・項別内訳：${c.national_item_rows??0} / 県へ対応する配分：${c.regional_rows} / 広域配分：${c.wide_area_rows??0} / 法人・枠別：${c.institution_rows??0}${c.parent?` / 母省：${c.parent}`:''}`),el('small',c.note));
    for(const n of c.research_notes??[]) {
      const detail=el('details');detail.append(el('summary',n.status),el('p',n.note));
      if(n.requested_url)detail.append(link('確認対象の公式入口 ↗',n.requested_url));
      if(n.retrieval_attempts?.length) {
        const attempts=el('details');attempts.append(el('summary','原本の取得試行と失敗種別'));
        for(const a of n.retrieval_attempts) {
          attempts.append(el('p',`${a.retrieved_at_utc ?? a.checked_at ?? '日時未収録'} / ${a.status} / ${a.failure_category ?? a.error_type ?? '取得結果'}${a.http_status ? ' / HTTP '+a.http_status : ''}`),link('対象原本 ↗',a.url));
        }
        detail.append(attempts);
      }
      if(n.next_steps?.length) {const list=el('ul');for(const step of n.next_steps)list.append(el('li',step));detail.append(list);}
      if(n.decision_checks?.length) for(const check of n.decision_checks)detail.append(el('p',`${check.dimension}：${check.status}`),el('small',check.finding));
      for(const id of n.source_ids)detail.append(sourceBlock(id));text.append(detail);
    }
    const observations=(data.research_observations ?? []).filter(o=>o.ministry===c.name);
    if(observations.length)text.append(observationTable(observations));
    row.append(el('strong',c.name),text);return row;
  }));
}
function exportCSV() {
  const fields=['region','prefecture','ministry','account','program','scope','basis','period2025','period2026','amount2025','amount2026','amount_status2025','amount_status2026','unit','comparability','evidence_status','legislator_mapping_status','note'];
  const lines=[fields.concat(['change_pct','candidate','screen_reasons','legislators','source_urls','legislator_evidence','original_evidence','municipality_mapping','geography_evidence','driver_checks']).map(csvCell).join(',')];
  for(const r of visible)lines.push(fields.map(f=>csvCell(r[f])).concat([
    csvCell(r.analysis.pct),csvCell(r.analysis.candidate),csvCell(r.analysis.reasons.join(' / ')),
    csvCell(rowMembers(r).map(memberLine).join(' / ')),csvCell(rowSourceIDs(r).map(id=>data.sources.find(s=>s.id===id)?.url).join(' ')),
    csvCell(JSON.stringify(rowMembers(r).map(m=>({name:m.name,roster_as_of:m.as_of,roster_url:m.source_url,party_status:m.party_status,party_evidence:m.party_evidence})))),
    csvCell(JSON.stringify(r.source_ids.map(id=>data.sources.find(s=>s.id===id)).filter(s=>s.original).map(s=>({source_id:s.id,url:s.original.url,retrieved_at_utc:s.original.retrieved_at_utc,sha256_original:s.original.sha256_original,verified_fields:[...new Set(s.original.verifications.filter(v=>v.status==='matched'&&v.row_ids.includes(r.id)).flatMap(v=>v.fields))]})))),
    csvCell(JSON.stringify(r.municipality_mapping ?? null)),
    csvCell(JSON.stringify(r.municipality_mapping ? [r.municipality_mapping.source_id,r.municipality_mapping.boundary_source_id].filter(Boolean).map(id=>data.sources.find(s=>s.id===id)).map(s=>({source_id:s.id,url:s.url,sha256_extracted_text:s.sha256_extracted_text,original:s.original})) : [])),
    csvCell(JSON.stringify(r.driver_checks ?? []))]).join(','));
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
    $('source-count').textContent=`公式出典 ${data.sources.length}件。両年度の原本数値照合 ${data.original_coverage.fully_verified_comparison_rows} / ${data.rows.length}行。`;
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
