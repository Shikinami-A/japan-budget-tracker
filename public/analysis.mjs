// Pure screening functions. Thresholds prioritize document checks, not allegations.
export function change(row) {
  const a = row.amount2025, b = row.amount2026;
  if (a === null || b === null) {
    const missingYears=[2025,2026].filter(year=>row[`amount${year}`]===null);
    const statuses=missingYears.map(year=>row[`amount_status${year}`] ?? '').join(' / ');
    const label=statuses.includes('原本ダッシュ') ? '原本ダッシュあり' :
      row.comparability==='片年度非掲載' ? '片年度非掲載' :
      row.comparability==='片年度未収載' || statuses.includes('未収載') ? '片年度未収載' : '片年度未取得';
    return {delta:null,pct:null,label};
  }
  const delta = b - a;
  if (a === 0) return { delta, pct: null, label: b === 0 ? '両年ゼロ' : '前年ゼロ・増減率なし' };
  const pct = row.published_change_pct ?? (delta / a * 100);
  return { delta, pct, label: `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%` };
}

export function peerChange(row, allRows) {
  const peers = allRows.filter(r => r.program === row.program && r.basis === row.basis &&
    r.account === row.account && r.scope === row.scope && r.region !== '全国' &&
    r.comparability === '同範囲' && r.amount2025 !== null && r.amount2026 !== null);
  if (row.region === '全国' || peers.length < 5) return null;
  const a = peers.reduce((sum, r) => sum + r.amount2025, 0);
  const b = peers.reduce((sum, r) => sum + r.amount2026, 0);
  return a > 0 ? (b - a) / a * 100 : null;
}

export function screen(row, allRows, thresholds = {}) {
  const { pct = 30, amount = 10, gap = 20 } = thresholds;
  const c = change(row), peer = peerChange(row, allRows);
  const deviation = c.pct === null || peer === null ? null : c.pct - peer;
  if (row.comparability !== '同範囲' || c.delta === null) {
    return { ...c, peer, deviation, candidate: false, reasons: ['比較条件の確認が先'] };
  }
  const reasons = [];
  if (Math.abs(c.delta) >= amount) {
    if (c.pct !== null && Math.abs(c.pct) >= pct) reasons.push(`増減率が±${pct}%以上`);
    if (deviation !== null && Math.abs(deviation) >= gap) reasons.push(`同制度の合計増減率との差が±${gap}ポイント以上`);
  }
  return { ...c, peer, deviation, candidate: reasons.length > 0, reasons };
}

export function membersFor(prefecture, members, includeProportional = false) {
  if (!prefecture || prefecture === '全国') return [];
  return members.filter(m => m.current_roster_eligible !== false && (m.prefectures.includes(prefecture) || (includeProportional &&
    (m.related_prefectures ?? []).includes(prefecture))));
}

export function membersForRow(row, members, includeProportional = true) {
  const candidates = membersFor(row.prefecture, members, includeProportional);
  const mapping = row.municipality_mapping;
  if (!mapping) return candidates;
  // Whole municipal amounts can relate to multiple constituencies. No amount
  // is divided among districts, and this is not a decision-time roster.
  return candidates.filter(m => m.chamber !== '衆議院' || m.election_type !== '小選挙区' ||
    mapping.districts.includes(m.district));
}

export function safeURL(value) {
  try {
    const u = new URL(value);
    return u.protocol === 'https:' && !u.username && !u.password ? u.href : null;
  } catch { return null; }
}

export function csvCell(value) {
  // Neutralize formulas even after leading spaces/control characters.
  let s = String(value ?? '');
  if (/^[\s\u0000-\u001f]*[=+\-@]/u.test(s)) s = "'" + s;
  return '"' + s.replaceAll('"', '""') + '"';
}

// One series is one institution, funding stage, scope, unit and pair of periods.
// Municipality and bureau totals are never inferred to be prefecture totals.
export function mapSeriesKey(row) {
  return JSON.stringify(['ministry','agency','program','scope','basis','account','period2025','period2026','unit'].map(k=>row[k] ?? null));
}

export function prefectureMap(rows, prefectures) {
  const direct = rows.filter(r=>prefectures.includes(r.prefecture) && r.region===r.prefecture);
  if (!direct.length) return {available:false, reason:'県別の原表掲載行がありません。市町村・広域管内の額から県計を推計しません。', entries:[]};
  if (new Set(direct.map(mapSeriesKey)).size!==1) return {available:false, reason:'制度と集計範囲を一つずつ選ぶと、県別の増減を地図に表示します。', entries:[]};
  const entries=prefectures.map(prefecture=>{
    const matches=direct.filter(r=>r.prefecture===prefecture);
    if (!matches.length) return {prefecture,row:null,pct:null,status:'表示条件に一致する県別行なし'};
    if (matches.length!==1) return {prefecture,row:null,pct:null,status:'県別行が重複・要確認'};
    const row=matches[0], c=change(row);
    if(c.pct===null) return {prefecture,row,pct:null,status:c.label};
    if(row.comparability!=='同範囲') return {prefecture,row,pct:null,status:`参考・比較条件を確認：${row.comparability}`};
    if(!['amount2025','amount2026'].every(k=>row.original_verified_fields?.includes(k))) return {prefecture,row,pct:null,status:'両年度の原本数値照合が未完了'};
    return {prefecture,row,pct:c.pct,status:c.label};
  });
  return {available:true, reason:null, entries};
}

export function changeColor(pct) {
  if(pct===null || !Number.isFinite(pct)) return null;
  const t=Math.min(Math.abs(pct)/10,1), neutral=[242,245,242];
  const edge=pct<0 ? [191,62,72] : [21,125,91];
  return `rgb(${neutral.map((v,i)=>Math.round(v+(edge[i]-v)*t)).join(',')})`;
}
