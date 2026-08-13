#!/usr/bin/env node
/* t1_shot.js — T1 清空按钮 UI 截图（离线静态源，无后端依赖） */
const fs = require('fs');
const DEBUG = 'http://127.0.0.1:9333';
const BASE = process.env.T1_BASE || 'http://127.0.0.1:8099/xiaohaigpt.html';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
class CDP {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); this.listeners = [];
    ws.addEventListener('message', (e) => this.onMsg(e)); }
  onMsg(e) { let m; try { m = JSON.parse(typeof e.data === 'string' ? e.data : Buffer.from(e.data).toString()); } catch { return; }
    if (m.id !== undefined && m.id !== null) { const p = this.pending.get(m.id); if (p) { this.pending.delete(m.id); if (m.error) p.reject(new Error(JSON.stringify(m.error))); else p.resolve(m.result || {}); } }
    else if (m.method) this.listeners.forEach((l) => l(m)); }
  send(method, params = {}) { return new Promise((resolve, reject) => { const id = ++this.id; this.pending.set(id, { resolve, reject }); this.ws.send(JSON.stringify({ id, method, params })); }); }
  on(method, fn) { this.listeners.push((m) => { if (m.method === method) fn(m.params); }); }
}
async function connect(wsUrl) { const ws = new WebSocket(wsUrl); await new Promise((res, rej) => { ws.addEventListener('open', res, { once: true }); ws.addEventListener('error', () => rej(new Error('ws fail')), { once: true }); }); return new CDP(ws); }
async function newTarget(url) { const r = await fetch(`${DEBUG}/json/new?${encodeURIComponent(url)}`, { method: 'PUT' }); return r.json(); }
async function evalJs(cdp, expression) { const r = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); return r.result ? r.result.value : undefined; }
async function shot(cdp, file) { const r = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false }); fs.writeFileSync(file, Buffer.from(r.data, 'base64')); }

(async () => {
  const t = await newTarget('about:blank');
  const cdp = await connect(t.webSocketDebuggerUrl);
  await cdp.send('Page.enable'); await cdp.send('Runtime.enable');
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await cdp.send('Page.navigate', { url: BASE });
  await sleep(2500);
  await shot(cdp, 'D:/CUC_Files/26新生网站/_t1_ui_mobile.png');
  const mobile = await evalJs(cdp, `(() => {
    const b = document.getElementById('clearBtn');
    const r = b.getBoundingClientRect();
    const ta = document.getElementById('input').getBoundingClientRect();
    return { btnText: b.textContent.trim(), btnW: Math.round(r.width), btnH: Math.round(r.height),
      inputW: Math.round(ta.width), visible: r.width > 0 && r.height > 0,
      footerOverlap: r.bottom <= window.innerHeight };
  })()`);
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false });
  await cdp.send('Page.navigate', { url: BASE });
  await sleep(2500);
  await shot(cdp, 'D:/CUC_Files/26新生网站/_t1_ui_desktop.png');
  const desktop = await evalJs(cdp, `(() => {
    const b = document.getElementById('clearBtn');
    const r = b.getBoundingClientRect();
    return { btnText: b.textContent.trim(), btnW: Math.round(r.width), btnH: Math.round(r.height), visible: r.width > 0 && r.height > 0 };
  })()`);
  console.log(JSON.stringify({ mobile, desktop }, null, 2));
  try { cdp.ws.close(); } catch {}
})().catch((e) => { console.error(e); process.exit(1); });
