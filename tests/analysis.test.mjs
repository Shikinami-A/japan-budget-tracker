import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {change,screen,peerChange,membersFor,safeURL,csvCell} from '../public/analysis.mjs';
const data=JSON.parse(readFileSync(new URL('../public/data.json',import.meta.url)));
const get=id=>data.rows.find(r=>r.id===id);
test('Rounded MLIT published ratios remain distinct from displayed-amount calculations',()=>{
  assert.equal(change(get('road-那須烏山市-0')).pct,-26.2);
  assert.equal(change(get('road-那須烏山市-2')).pct,20.5);
  assert.equal(change(get('road-那珂川町-0')).pct,-54.1);
});
test('Non-comparable supplementary comparisons never become automatic candidates',()=>{
  const rows=data.rows.filter(r=>r.comparability.startsWith('参考'));
  assert.ok(rows.length>0);
  assert.ok(rows.every(r=>!screen(r,data.rows,{pct:0,amount:0,gap:0}).candidate));
});
test('Zero baselines and absent values do not invent percentage changes',()=>{
  assert.equal(change({amount2025:0,amount2026:100}).pct,null);
  assert.equal(change({amount2025:null,amount2026:100}).delta,null);
  assert.equal(change({amount2025:100,amount2026:0}).pct,-100);
});
test('Peer baseline uses the matching institution and scope, not unweighted regional rates',()=>{
  const r=get('lat-愛知県-0'),actual=peerChange(r,data.rows);
  const peers=data.rows.filter(p=>p.program===r.program&&p.scope===r.scope);
  const a=peers.reduce((s,p)=>s+p.amount2025,0),b=peers.reduce((s,p)=>s+p.amount2026,0);
  assert.equal(actual,(b-a)/a*100);
  assert.ok(screen(r,data.rows).candidate);
  assert.ok(r.explanation_source_ids.length);
});
test('A proportional legislator is never assigned to a local constituency',()=>{
  const yana=data.legislators.find(m=>m.name==='簗和生');
  assert.equal(yana.district,'（比）北関東');
  assert.ok(!membersFor('栃木県',data.legislators).includes(yana));
  assert.ok(membersFor('栃木県',data.legislators,true).includes(yana));
});
test('Executable and credential-bearing URLs are rejected; spreadsheet formulas are neutralized',()=>{
  for(const u of ['javascript:alert(1)','data:text/html,abc','http://example.com','https://user:pass@example.com'])assert.equal(safeURL(u),null);
  assert.equal(safeURL('https://www.mof.go.jp/'),'https://www.mof.go.jp/');
  for(const s of ['=HYPERLINK("x")','  +cmd','\t@SUM(A1)','-1+2'])assert.ok(csvCell(s).startsWith('"\''));
  assert.equal(csvCell('a"b'),'"a""b"');
});
test('Public UI has restrictive CSP and no HTML interpretation of source text',()=>{
  const html=readFileSync(new URL('../public/index.html',import.meta.url),'utf8');
  const js=readFileSync(new URL('../public/app.mjs',import.meta.url),'utf8');
  assert.ok(html.includes("default-src 'none'"));
  assert.ok(!js.includes('innerHTML')&&!js.includes('eval('));
});
