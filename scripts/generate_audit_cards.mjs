/** Render every published audit row, including losses, at X card dimensions.
 * Usage: node scripts/generate_audit_cards.mjs [source.json] [output-dir]
 * The SHA-256 shown is a digest of the canonical published row. It is not a
 * substitute for the independent quote verification receipt.
 */
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import vm from 'node:vm';
import sharp from 'sharp';

const source = process.argv[2] || 'viewer/clv_audit_report.json';
const destination = process.argv[3] || 'viewer/share/audit';
const report = JSON.parse(await readFile(source, 'utf8'));
const rows = report.records || report.ledger || report.rows;
if (!Array.isArray(rows) || !rows.length) throw new Error('No audit rows');
await mkdir(destination, { recursive: true });
const slatePath = process.argv[4] || (report.season && report.week
  ? `viewer/slate_${report.season}_w${report.week}.json` : 'viewer/slate_2026_w7.json');
let modelRows = [];
try { modelRows = JSON.parse(await readFile(slatePath, 'utf8')); } catch (_) {}
const mathContext = { window: {} };
vm.runInNewContext(await readFile('viewer/js/quant-math.js','utf8'), mathContext);
const math = mathContext.window.PropSteinMath.createQuantMath({ alpha: 0.1097, k: 1.555 });
function modelRange(row) {
  const modelRow = modelRows.find(r => row.player_id && r.player_id === row.player_id)
    || modelRows.find(r => String(r.player_display_name).toLowerCase() === String(row.player || row.player_name).toLowerCase());
  if (!modelRow) return null;
  const market = String(row.market || '').toLowerCase();
  const rec = market === 'receptions' || market === 'rec';
  const muRec = Number(modelRow.mu_rec), muYpr = Number(modelRow.mu_ypr);
  if (!Number.isFinite(muRec) || (!rec && !Number.isFinite(muYpr))) return null;
  const over = (x) => rec ? math.pRecOver(muRec,x) : math.pYardsOver(muRec,muYpr,x);
  const max = rec ? 30 : 300;
  const quantile = (probability) => {
    let low = 0, high = max;
    for (let n=0;n<24;n++) {
      const mid=(low+high)/2;
      if (1-over(mid) < probability) low=mid; else high=mid;
    }
    return Math.round(high * 10) / 10;
  };
  const p10 = quantile(0.10), p90 = quantile(0.90);
  const spread = Math.max(1, p90-p10);
  const lower = Math.max(0,p10-spread*0.3), upper = p90+spread*0.3;
  const xs = rec
    ? Array.from({length:Math.ceil(upper)-Math.floor(lower)+1},(_,i)=>Math.floor(lower)+i)
    : Array.from({length:25},(_,i)=>lower+(upper-lower)*i/24);
  const step = rec ? 1 : (upper-lower)/24;
  const density = xs.map(x=>Math.max(0,rec
    ? (x <= 0 ? 1-over(0) : over(x-1)-over(x))
    : over(Math.max(0,x-step/2))-over(x+step/2)));
  const peak = Math.max(...density,0.000001);
  const curve = xs.map((x,i)=>`${i ? 'L' : 'M'}${(760+(x-lower)/(upper-lower)*330).toFixed(1)} ${(480-density[i]/peak*110).toFixed(1)}`).join(' ');
  return { p10, p90, curve };
}
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;' })[c]);
const hash = (value) => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const cohortLabel = report.records ? 'PROPSTEIN  /  LEGACY 8-ROW LEDGER'
  : report.rows ? 'PROPSTEIN  /  SETTLED SLATE' : 'PROPSTEIN  /  PUBLIC CLOSE AUDIT';
const text = (x,y,value,size=32,color='#eef1f8',weight=500) =>
  `<text x="${x}" y="${y}" fill="${color}" font-size="${size}" font-family="Arial,sans-serif" font-weight="${weight}">${esc(value)}</text>`;
const frame = (body) => `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630"><rect width="1200" height="630" fill="#090a0f"/><rect x="38" y="38" width="1124" height="554" rx="22" fill="#12141c" stroke="#272b38"/><path d="M80 106H1120" stroke="#272b38"/>${text(80,86,cohortLabel,24,'#a5e4fc',700)}${body}${text(80,555,'Historical research · full record at propstein.com/performance/',22,'#a8b2c5')}${text(998,555,'propstein.com',18,'#a5e4fc')}</svg>`;
async function png(name, svg) {
  const file = path.join(destination, name);
  await sharp(Buffer.from(svg)).png().toFile(file);
  return file;
}
const manifest = [];
for (const [index,row] of rows.entries()) {
  const digest = hash(row);
  const outcome = row.outcome || row.result || row.status || 'UNSETTLED';
  const delta = row.clv_yards ?? row.line_movement;
  const range = modelRange(row);
  const brierSkill = Number(row.brier_market) > 0 ? 1 - Number(row.brier_model) / Number(row.brier_market) : null;
  const rangeText = range ? `Model P10–P90: ${range.p10.toFixed(1)}–${range.p90.toFixed(1)}` : 'Model P10–P90: unavailable';
  const brierText = brierSkill === null ? 'Brier skill: unavailable' : `Brier skill: ${(brierSkill * 100).toFixed(1)}%`;
  const curve = range ? `<path d="${range.curve}" fill="none" stroke="#a5e4fc" stroke-width="4"/><path d="M760 480H1090" stroke="#272b38" stroke-width="2"/>${text(760,502,`P10 ${range.p10.toFixed(1)}`,17,'#a8b2c5')}${text(1010,502,`P90 ${range.p90.toFixed(1)}`,17,'#a8b2c5')}` : '';
  const name = `result-${String(index + 1).padStart(3,'0')}.png`;
  const body = [
    text(80,168,`${row.player || row.player_name || 'Unknown'} · ${row.market || 'market'}`,38,'#eef1f8',700),
    text(80,230,`${row.side || '—'} ${row.line ?? row.trigger_line ?? row.quote?.line ?? '—'} · ${outcome}`,30,outcome === 'LOSS' || outcome === 'CLOSED_WORSE' ? '#e9bb78' : '#a5e4fc',700),
    text(80,305,`Close delta: ${delta == null ? 'unavailable' : Number(delta).toFixed(2)}`,30),
    text(80,353,`Receipt ID: ${row.settlement_receipt || row.receipt_id || 'unavailable'}`,22,'#a8b2c5'),
    text(80,423,`SHA-256 record digest: ${digest.slice(0,32)}`,20,'#a8b2c5'),
    text(80,452,digest.slice(32),20,'#a8b2c5'),
    text(80,486,`${brierText} · ${rangeText}`,19,'#a8b2c5'),
    curve,
  ].join('');
  await png(name, frame(body));
  manifest.push({ file: name, outcome, receipt_id: row.settlement_receipt || row.receipt_id || null, record_sha256: digest, model_range: range ? { p10: range.p10, p90: range.p90 } : null });
}
const summary = report.summary || report;
const summaryHash = hash({ summary, rows });
const summaryBrierSkill = Number(report.brier_market) > 0 ? 1 - Number(report.brier_model) / Number(report.brier_market) : null;
const beatClosePct = Number.isFinite(Number(report.beat_close_rate_pct)) ? Number(report.beat_close_rate_pct)
  : Number.isFinite(Number(summary.positive_clv_pct)) ? Number(summary.positive_clv_pct) * 100
  : Number(summary.total_graded) > 0 ? Number(summary.beat_close_count) / Number(summary.total_graded) * 100
  : null;
await png('weekly-summary.png', frame([
  text(80,180,`Audit summary · ${report.slate || (report.season && report.week ? `${report.season}-W${String(report.week).padStart(2,'0')}` : 'published cohort')}`,40,'#eef1f8',700),
  text(80,245,`Graded: ${summary.total_graded ?? summary.graded_count ?? rows.length} · Beat close: ${beatClosePct === null ? 'unavailable' : `${beatClosePct.toFixed(1)}%`}`,30),
  text(80,305,`Average line delta: ${summary.avg_line_delta ?? summary.avg_clv_yards ?? 'unavailable'}`,30),
  text(80,365,`Brier skill: ${summaryBrierSkill === null ? 'unavailable' : `${(summaryBrierSkill * 100).toFixed(1)}%`} · P10–P90: per result cards`,22,'#a8b2c5'),
  text(80,423,`SHA-256 cohort digest: ${summaryHash.slice(0,32)}`,20,'#a8b2c5'),
  text(80,452,summaryHash.slice(32),20,'#a8b2c5'),
].join('')));
await writeFile(path.join(destination,'manifest.json'), JSON.stringify({ source, generated_at: new Date().toISOString(), record_count: rows.length, cards: manifest, summary_sha256: summaryHash },null,2));
const caveat = report.records ? 'This legacy hand ledger cannot be combined with the full-board cohort without matching event IDs.' : 'These cards cover every row in this published cohort.';
const gallery = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PropStein ${esc(cohortLabel)} graphics</title><link rel="stylesheet" href="/public.css"></head><body><main style="max-width:960px;margin:auto;padding:24px"><p><a href="/performance/">← Public performance record</a></p><h1>${esc(cohortLabel)}</h1><p>Historical descriptive graphics, including losing results. The SHA-256 on each card is a digest of its published data row, not a sportsbook quote verification receipt. ${esc(caveat)}</p><h2>Summary</h2><a href="weekly-summary.png"><img src="weekly-summary.png" alt="Audit summary card" style="max-width:100%;height:auto"></a><h2>All ${rows.length} results</h2>${manifest.map((card,index)=>`<section style="margin:24px 0"><a href="${card.file}"><img src="${card.file}" loading="lazy" alt="${esc(rows[index].player || rows[index].player_name || 'Player')} ${esc(card.outcome)} audit graphic" style="max-width:100%;height:auto"></a><p>${esc(rows[index].player || rows[index].player_name || 'Player')} · ${esc(card.outcome)} · <a href="${card.file}">Open PNG</a></p></section>`).join('')}</main></body></html>`;
await writeFile(path.join(destination,'index.html'), gallery);
console.log(`Rendered ${rows.length} results and one summary to ${destination}`);
