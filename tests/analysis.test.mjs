import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {change,screen,peerChange,membersFor,membersForRow,safeURL,csvCell} from '../public/analysis.mjs';
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
test('Verified municipal boundaries restrict House constituencies while preserving Senate and proportional relationships',()=>{
  const local=membersForRow(get('road-那珂川町-0'),data.legislators);
  assert.ok(local.some(m=>m.name==='渡辺真太朗'));
  assert.ok(local.some(m=>m.name==='簗和生'&&m.election_type==='比例代表'));
  assert.ok(local.filter(m=>m.chamber==='衆議院'&&m.election_type==='小選挙区').every(m=>m.district==='栃木3'));
  assert.ok(local.some(m=>m.chamber==='参議院'));
  const split=data.municipality_mappings.find(m=>m.municipality==='宇都宮市');
  const across=membersForRow({prefecture:'栃木県',municipality_mapping:split},data.legislators);
  assert.deepEqual(new Set(across.filter(m=>m.chamber==='衆議院'&&m.election_type==='小選挙区').map(m=>m.district)),new Set(['栃木1','栃木2']));
  assert.deepEqual(membersForRow({prefecture:'愛知県'},data.legislators),membersFor('愛知県',data.legislators,true));
});
test('Uncollected third-round allocations remain missing and never enter screening',()=>{
  const rows=data.rows.filter(r=>r.program==='特定防衛施設周辺整備調整交付金（第3回）');
  assert.equal(rows.length,122);
  assert.ok(rows.every(r=>r.amount2025===null&&r.amount_status2025.startsWith('未収載')));
  assert.ok(rows.every(r=>!screen(r,data.rows,{pct:0,amount:0,gap:0}).candidate));
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
