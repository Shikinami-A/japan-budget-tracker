// Pure screening functions. Thresholds prioritize document checks, not allegations.
export function change(row) {
  const a = row.amount2025, b = row.amount2026;
  if (a === null || b === null) return { delta: null, pct: null, label: row.comparability === '片年度非掲載' ? '片年度非掲載' : '片年度未取得' };
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
  return members.filter(m => m.prefectures.includes(prefecture) || (includeProportional &&
    (m.related_prefectures ?? []).includes(prefecture)));
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
