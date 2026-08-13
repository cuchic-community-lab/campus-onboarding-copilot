#!/usr/bin/env node
/* eslint-disable no-console */
/**
 * browser_cdp_driver.js — XiaohaiGPT 浏览器视觉层自检驱动（Edge headless + CDP）
 *
 * 零依赖：Node 22 原生 WebSocket + fetch + fs。
 * 对每个用例：创建独立 target → 注入清 localStorage（隔离聊天历史）→ 设置 viewport →
 * 输入问题 → 点击发送 → 等待回答渲染完成 → 采集 DOM 元数据 → 视口截图 →
 * （可选）展开引用区 + 滚动到底 → 第二张截图 →（可选）师哥师姐链路点按钮等 pending。
 *
 * 用法: node browser_cdp_driver.js <config.json>
 * config 字段:
 *   debugPort  : Edge CDP 端口
 *   baseUrl    : 被测页面 URL
 *   outDir     : 截图输出目录
 *   viewportMobile : {w,h,dsf}
 *   viewportDesktop: {w,h,dsf}
 *   cases      : [{id, query, viewport:'mobile'|'desktop', expand:bool, action:'chat'|'senior'}]
 * 输出: stdout 单行 JSON 数组（每用例 DOM 元数据 + 截图路径）
 */

const fs = require('fs');

const config = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const BASE = config.baseUrl;
const OUT = config.outDir;
const DEBUG = `http://127.0.0.1:${config.debugPort}`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class CDP {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    this.listeners = [];
    ws.addEventListener('message', (e) => this.onMsg(e));
  }
  onMsg(e) {
    let m;
    try { m = JSON.parse(typeof e.data === 'string' ? e.data : Buffer.from(e.data).toString()); } catch { return; }
    if (m.id !== undefined && m.id !== null) {
      const p = this.pending.get(m.id);
      if (p) {
        this.pending.delete(m.id);
        if (m.error) p.reject(new Error('CDP error: ' + JSON.stringify(m.error)));
        else p.resolve(m.result || {});
      }
    } else if (m.method) {
      this.listeners.forEach((l) => l(m));
    }
  }
  send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++this.id;
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  on(method, fn) { this.listeners.push((m) => { if (m.method === method) fn(m.params); }); }
}

async function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true });
    ws.addEventListener('error', () => reject(new Error('ws open failed')), { once: true });
  });
  return new CDP(ws);
}

async function newTarget(url) {
  const res = await fetch(`${DEBUG}/json/new?${encodeURIComponent(url)}`, { method: 'PUT' });
  if (!res.ok) throw new Error('json/new failed: ' + res.status);
  return res.json();
}

async function closeTarget(id) {
  try { await fetch(`${DEBUG}/json/close/${id}`, { method: 'PUT' }); } catch { /* ignore */ }
}

async function waitFor(fn, timeoutMs, label) {
  const t0 = Date.now();
  for (;;) {
    try {
      const v = await fn();
      if (v) return v;
    } catch { /* keep waiting */ }
    if (Date.now() - t0 > timeoutMs) throw new Error('waitFor timeout: ' + label);
    await sleep(250);
  }
}

async function evalJs(cdp, expression) {
  const r = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error('eval exception: ' + JSON.stringify(r.exceptionDetails).slice(0, 200));
  return r.result ? r.result.value : undefined;
}

async function screenshot(cdp, file) {
  const r = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  fs.writeFileSync(file, Buffer.from(r.data, 'base64'));
  return file;
}

/* 输入问题到 textarea 并触发 input（解锁发送按钮） */
function inputScript(q) {
  return `(() => {
    const ta = document.getElementById('input');
    if (!ta) return 'no-input';
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
    setter.call(ta, ${JSON.stringify(q)});
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    return ta.value;
  })()`;
}

/* 采集 DOM 元数据（供报告与识图交叉验证） */
const META_SCRIPT = `(() => {
  const q = (s) => document.querySelector(s);
  const qa = (s) => Array.from(document.querySelectorAll(s));
  const bubbleTexts = qa('#chatWrap .bubble').map((e) => e.textContent || '');
  const lastAi = qa('.msg.ai .bubble.md').pop();
  const lastBubble = qa('#chatWrap .bubble').pop();
  const imgs = qa('#chatWrap img').map((i) => ({
    src: (i.src || '').slice(0, 90),
    complete: i.complete, naturalWidth: i.naturalWidth,
    fail: !!(i.closest('.ve-img-fail'))
  }));
  return {
    healthClass: (q('#hdStatus') || {}).className || '',
    healthTxt: (q('#hdStatusTxt') || {}).textContent || '',
    hbClass: (q('#healthBar') || {}).className || '',
    hbTxt: (q('#healthTxt') || {}).textContent || '',
    msgCount: qa('#chatWrap > .msg').length,
    aiMsgs: qa('#chatWrap > .msg.ai').length,
    userMsgs: qa('#chatWrap > .msg.user').length,
    seniorMsgs: qa('#chatWrap > .msg.senior').length,
    sysMsgs: qa('.msg.sys .bubble').map((e) => e.textContent),
    unknownCards: qa('.unknown-card').length,
    sourceTags: qa('.source-tag').map((e) => e.textContent.trim()),
    citeDetails: qa('details.cite-details').map((d) => (d.querySelector('summary') || {}).textContent || '').slice(0, 4),
    citeCards: qa('.cite-card').length,
    citeAuthBadges: qa('.cite-authority').map((e) => e.textContent.trim()).slice(0, 10),
    veCards: qa('.ve-card').length,
    veImgFail: qa('.ve-img-fail').length,
    veImgs: imgs,
    seniorCta: qa('.senior-cta button').map((b) => b.getAttribute('data-senior-ask')),
    errBubbles: bubbleTexts.filter((t) => /(连接失败|请求失败|TypeError|ReferenceError|HTTP \\d)/.test(t)).slice(0, 4),
    lastAiTextHead: (lastAi ? lastAi.textContent : '').replace(/\\s+/g, ' ').slice(0, 160),
    lastBubbleTextHead: (lastBubble ? lastBubble.textContent : '').replace(/\\s+/g, ' ').slice(0, 100),
    deviceId: (document.cookie.match(/xh_device_id=([^;]+)/) || [])[1] || ''
  };
})()`;

async function runCase(c) {
  const t = await newTarget('about:blank');
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true });
    ws.addEventListener('error', () => reject(new Error('case ws open failed')), { once: true });
  });
  const cdp = new CDP(ws);
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');

  /* 隔离：每个用例新页面，清掉聊天历史 localStorage（保留 device_id cookie，模拟同一设备） */
  await cdp.send('Page.addScriptToEvaluateOnNewDocument', {
    source: `try { localStorage.removeItem('xh_chat_history'); } catch (e) {}`
  });

  const vp = c.viewport === 'desktop' ? config.viewportDesktop : config.viewportMobile;
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: vp.w, height: vp.h, deviceScaleFactor: vp.dsf || 1, mobile: c.viewport !== 'desktop'
  });

  await cdp.send('Page.navigate', { url: BASE });
  await waitFor(
    () => evalJs(cdp, `document.readyState === 'complete'`),
    20000, 'page-load'
  );
  /* 模型状态指示灯就绪（绿/黄/红） */
  await waitFor(
    () => evalJs(cdp, `/ok|warn|err/.test((document.querySelector('#hdStatus')||{}).className || '')`),
    15000, 'model-health'
  );

  const meta = { id: c.id, query: c.query, viewport: c.viewport };

  /* 输入并发送 */
  await evalJs(cdp, inputScript(c.query));
  await waitFor(() => evalJs(cdp, `!document.getElementById('sendBtn').disabled`), 5000, 'send-btn-enabled');
  await evalJs(cdp, `document.getElementById('sendBtn').click()`);

  /* 等待回答渲染完成：typing 消失 + 发送按钮恢复 */
  await waitFor(
    () => evalJs(cdp, `(() => { const d = document.getElementById('sendBtn').disabled; const ty = !!document.querySelector('.typing'); return !d && !ty; })()`),
    90000, 'answer-rendered'
  );
  await sleep(800); /* 让滚动/懒加载图片稳定 */

  meta.dom = await evalJs(cdp, META_SCRIPT);

  /* 截图 1：视口 */
  const shot1 = `${OUT}/${c.id}.png`;
  await screenshot(cdp, shot1);
  meta.shots = [shot1];

  /* 截图 2：展开引用区 + 滚动到底（关键页面） */
  if (c.expand) {
    await evalJs(cdp, `(() => {
      document.querySelectorAll('details.cite-details').forEach((d) => { d.open = true; });
      const main = document.getElementById('chatArea');
      if (main) main.scrollTop = main.scrollHeight;
    })()`);
    await sleep(900);
    const shot2 = `${OUT}/${c.id}_expanded.png`;
    await screenshot(cdp, shot2);
    meta.shots.push(shot2);
  }

  /* 师哥师姐链路：点"问题没有解决？问师哥师姐"→ 等 pending 占位 → 查后端状态 */
  if (c.action === 'senior') {
    await evalJs(cdp, `(() => {
      const b = document.querySelector('.senior-cta button[data-senior-ask]');
      if (b) b.click();
    })()`);
    let pending = false;
    try {
      await waitFor(() => evalJs(cdp, `document.querySelectorAll('.msg.senior').length > 0`), 15000, 'senior-pending');
      pending = true;
      await sleep(1200);
      const shotS = `${OUT}/${c.id}_senior.png`;
      await screenshot(cdp, shotS);
      meta.shots.push(shotS);
    } catch (e) { meta.seniorError = String(e.message); }
    /* 后端确认 answered 状态（复用页面 cookie 的 device_id） */
    try {
      const dev = meta.dom.deviceId;
      if (dev) {
        const res = await fetch(`${config.apiBase || 'http://127.0.0.1:8000'}/api/senior/answers?device_id=${encodeURIComponent(dev)}`);
        const data = await res.json();
        const qs = (data && data.questions) || [];
        meta.seniorStatus = qs.map((q) => ({ question_id: q.question_id, status: q.status, replied: !!(q.reply && q.reply.text) })).slice(0, 5);
      }
    } catch (e) { meta.seniorApiError = String(e.message); }
    meta.seniorPending = pending;
  }

  ws.close();
  await closeTarget(t.id);
  return meta;
}

(async () => {
  try {
    const results = [];
    for (const c of config.cases) {
      try {
        results.push(await runCase(c));
      } catch (e) {
        results.push({ id: c.id, query: c.query, viewport: c.viewport, error: String(e.message) });
      }
    }
    process.stdout.write(JSON.stringify(results, null, 2));
  } catch (e) {
    process.stderr.write('FATAL: ' + e.stack + '\n');
    process.exit(1);
  }
})();
