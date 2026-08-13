#!/usr/bin/env node
/* eslint-disable no-console */
/**
 * t1_clear_test.js — T1 清空会话前端链路自检（Edge headless + CDP，零依赖）
 *
 * 模式：
 *   离线（T1_CHAT_MOCK=1 默认）：CDP Fetch mock /api/chat + /api/model/check + /api/session/clear
 *     验证 S1 成功（清 DOM/存储/cookie/提示/刷新无复活）、S3 失败（mock 500 → 提示且不清数据）
 *   真实（T1_CHAT_MOCK=0）：真实 /api/chat；clear 默认走真实后端（T1_CLEAR_REAL=1），
 *     额外验证第三问"那床多大"的 chat 请求体不再携带旧 session_id（前端上下文已断）；
 *     设 T1_CLEAR_REAL=0 则 clear 用 mock 500 测真实页面下的失败处理。
 *
 * 用法:
 *   node t1_clear_test.js                                              # 离线
 *   T1_CHAT_MOCK=0 T1_BASE=http://127.0.0.1:8000/xiaohaigpt.html node t1_clear_test.js   # 真实端到端
 */
const fs = require('fs');

const DEBUG = 'http://127.0.0.1:9333';
const BASE = process.env.T1_BASE || 'http://127.0.0.1:8099/xiaohaigpt.html';
const CHAT_MOCK = process.env.T1_CHAT_MOCK !== '0';
const CLEAR_REAL = process.env.T1_CLEAR_REAL !== '0';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class CDP {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); this.listeners = [];
    ws.addEventListener('message', (e) => this.onMsg(e)); }
  onMsg(e) {
    let m; try { m = JSON.parse(typeof e.data === 'string' ? e.data : Buffer.from(e.data).toString()); } catch { return; }
    if (m.id !== undefined && m.id !== null) {
      const p = this.pending.get(m.id);
      if (p) { this.pending.delete(m.id); if (m.error) p.reject(new Error('CDP error: ' + JSON.stringify(m.error))); else p.resolve(m.result || {}); }
    } else if (m.method) { this.listeners.forEach((l) => l(m)); }
  }
  send(method, params = {}) { return new Promise((resolve, reject) => {
    const id = ++this.id; this.pending.set(id, { resolve, reject });
    this.ws.send(JSON.stringify({ id, method, params })); }); }
  on(method, fn) { this.listeners.push((m) => { if (m.method === method) fn(m.params); }); }
}

async function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  await new Promise((res, rej) => { ws.addEventListener('open', res, { once: true }); ws.addEventListener('error', () => rej(new Error('ws open failed')), { once: true }); });
  return new CDP(ws);
}
async function newTarget(url) {
  const res = await fetch(`${DEBUG}/json/new?${encodeURIComponent(url)}`, { method: 'PUT' });
  if (!res.ok) throw new Error('json/new failed: ' + res.status);
  return res.json();
}
async function closeTarget(id) { try { await fetch(`${DEBUG}/json/close/${id}`, { method: 'PUT' }); } catch { /* ignore */ } }
async function waitFor(fn, timeoutMs, label) {
  const t0 = Date.now();
  for (;;) {
    try { const v = await fn(); if (v) return v; } catch { /* keep */ }
    if (Date.now() - t0 > timeoutMs) throw new Error('waitFor timeout: ' + label);
    await sleep(250);
  }
}
async function evalJs(cdp, expression) {
  const r = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error('eval exception: ' + JSON.stringify(r.exceptionDetails).slice(0, 300));
  return r.result ? r.result.value : undefined;
}

const inputScript = (q) => `(() => {
  const ta = document.getElementById('input');
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
  setter.call(ta, ${JSON.stringify(q)});
  ta.dispatchEvent(new Event('input', { bubbles: true }));
  return ta.value;
})()`;

const SNAPSHOT = `(() => {
  const qa = (s) => Array.from(document.querySelectorAll(s));
  const bubbleTexts = qa('#chatWrap .bubble').map((e) => e.textContent || '');
  return {
    msgCount: qa('#chatWrap > .msg').length,
    userMsgs: qa('#chatWrap > .msg.user').length,
    aiMsgs: qa('#chatWrap > .msg.ai').length,
    sysMsgs: qa('#chatWrap > .msg.sys .bubble').map((e) => e.textContent),
    lsHistory: (() => { try { return localStorage.getItem('xh_chat_history'); } catch (e) { return 'ERR'; } })(),
    hasQuestionsCookie: document.cookie.indexOf('xh_questions=') >= 0,
    hasDeviceCookie: document.cookie.indexOf('xh_device_id=') >= 0,
    clearBtnDisabled: document.getElementById('clearBtn').disabled,
    lastBubble: (bubbleTexts[bubbleTexts.length - 1] || '').slice(0, 60)
  };
})()`;

/* 打开页面：清本地存储隔离（保留 device_id cookie），等就绪；返回监控句柄 */
async function openPage(cdp, clearMockStatus) {
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Page.addScriptToEvaluateOnNewDocument', {
    source: `try { localStorage.removeItem('xh_chat_history'); } catch (e) {}`
  });
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });

  let clearIntercepted = 0;
  let clearBody = null;
  const chatBodies = [];

  if (CHAT_MOCK) {
    /* 离线：Fetch 拦截 mock */
    await cdp.send('Fetch.enable', { patterns: [
      { urlPattern: '*api/session/clear*', requestStage: 'Request' },
      { urlPattern: '*api/chat*', requestStage: 'Request' },
      { urlPattern: '*api/model/check*', requestStage: 'Request' }
    ] });
    cdp.on('Fetch.requestPaused', async (p) => {
      try {
        const url = p.request.url || '';
        if (url.indexOf('/api/session/clear') >= 0) {
          clearIntercepted++; clearBody = p.request.postData || '';
          const body = clearMockStatus === 200 ? JSON.stringify({ status: 'ok', cleared: 'all' }) : JSON.stringify({ error: 'boom' });
          await cdp.send('Fetch.fulfillRequest', { requestId: p.requestId, responseCode: clearMockStatus,
            responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
            body: Buffer.from(body).toString('base64') });
        } else if (url.indexOf('/api/model/check') >= 0) {
          await cdp.send('Fetch.fulfillRequest', { requestId: p.requestId, responseCode: 200,
            responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
            body: Buffer.from(JSON.stringify({ ok: true, provider: 'mock', model: 'mock', composer: 'openai_compatible' })).toString('base64') });
        } else if (url.indexOf('/api/chat') >= 0) {
          chatBodies.push(p.request.postData || '');
          await cdp.send('Fetch.fulfillRequest', { requestId: p.requestId, responseCode: 200,
            responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
            body: Buffer.from(JSON.stringify({ answer: '（离线测试回答）宿舍以 4 人间为主，具体以官方通知为准。',
              session_id: 'mock-sess-001', composer: 'extractive_fallback', source: '', citation_metadata: {}, visual_evidence: [] })).toString('base64') });
        } else {
          await cdp.send('Fetch.continueRequest', { requestId: p.requestId });
        }
      } catch (e) { console.error('fetch fulfill failed', e.message); }
    });
  } else {
    /* 真实：Network 监听记录 /api/chat 请求体；clear 视 CLEAR_REAL 决定是否 mock */
    await cdp.send('Network.enable');
    cdp.on('Network.requestWillBeSent', (p) => {
      const url = p.request.url || '';
      if (url.indexOf('/api/chat') >= 0) chatBodies.push(p.request.postData || '');
      if (url.indexOf('/api/session/clear') >= 0) {
        clearIntercepted++; clearBody = p.request.postData || '';
      }
    });
    if (!CLEAR_REAL) {
      await cdp.send('Fetch.enable', { patterns: [{ urlPattern: '*api/session/clear*', requestStage: 'Request' }] });
      cdp.on('Fetch.requestPaused', async (p) => {
        try {
          clearIntercepted++; clearBody = p.request.postData || '';
          const body = clearMockStatus === 200 ? JSON.stringify({ status: 'ok', cleared: 'all' }) : JSON.stringify({ error: 'boom' });
          await cdp.send('Fetch.fulfillRequest', { requestId: p.requestId, responseCode: clearMockStatus,
            responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
            body: Buffer.from(body).toString('base64') });
        } catch (e) { console.error('clear fulfill failed', e.message); }
      });
    }
  }

  /* confirm 弹窗自动 accept */
  cdp.on('Page.javascriptDialogOpening', async () => {
    try { await cdp.send('Page.handleJavaScriptDialog', { accept: true }); } catch (e) { /* ignore */ }
  });

  await cdp.send('Page.navigate', { url: BASE });
  await waitFor(() => evalJs(cdp, `document.readyState === 'complete'`), 20000, 'page-load');
  await waitFor(() => evalJs(cdp, `/ok|warn|err/.test((document.querySelector('#hdStatus')||{}).className || '')`), 20000, 'model-health');
  return { get clearIntercepted() { return clearIntercepted; }, get clearBody() { return clearBody; }, get chatBodies() { return chatBodies; } };
}

async function ask(cdp, q) {
  await evalJs(cdp, inputScript(q));
  await waitFor(() => evalJs(cdp, `!document.getElementById('sendBtn').disabled`), 5000, 'send-btn-enabled');
  await evalJs(cdp, `document.getElementById('sendBtn').click()`);
  await waitFor(
    () => evalJs(cdp, `(() => { const d = document.getElementById('sendBtn').disabled; const ty = !!document.querySelector('.typing'); return !d && !ty; })()`),
    120000, 'answer-rendered'
  );
  await sleep(500);
}

async function runCase(mockStatus, label, opts) {
  const { extraThirdAsk } = opts || {};
  const t = await newTarget('about:blank');
  const cdp = await connect(t.webSocketDebuggerUrl);
  const state = await openPage(cdp, mockStatus);

  await ask(cdp, '宿舍是几人间？');
  await ask(cdp, 'GPA 怎么算');
  const before = await evalJs(cdp, SNAPSHOT);

  await evalJs(cdp, `document.getElementById('clearBtn').click()`);

  let after = null;
  if (mockStatus === 200) {
    await waitFor(() => evalJs(cdp, `(() => {
      const sys = Array.from(document.querySelectorAll('#chatWrap > .msg.sys .bubble')).map(e => e.textContent);
      return sys.some(t => t.indexOf('会话已清空') >= 0);
    })()`), 10000, 'clear-success-msg');
    await sleep(300);
    after = await evalJs(cdp, SNAPSHOT);

    await cdp.send('Page.reload', { ignoreCache: true });
    await waitFor(() => evalJs(cdp, `document.readyState === 'complete'`), 20000, 'reload-complete');
    await sleep(800);
    const afterReload = await evalJs(cdp, SNAPSHOT);

    let thirdAsk = null;
    if (extraThirdAsk) {
      const chatCountBefore = state.chatBodies.length;
      await ask(cdp, '那床多大');
      const newBodies = state.chatBodies.slice(chatCountBefore);
      thirdAsk = { body: newBodies[newBodies.length - 1] || '', snapshot: await evalJs(cdp, SNAPSHOT) };
    }

    wsClose(cdp, t.id);
    return { label, mockStatus, mode: CHAT_MOCK ? 'offline' : 'real', before, after, afterReload, thirdAsk,
      clearIntercepted: state.clearIntercepted, clearBody: state.clearBody, chatBodies: state.chatBodies };
  }

  await waitFor(() => evalJs(cdp, `(() => {
    const sys = Array.from(document.querySelectorAll('#chatWrap > .msg.sys .bubble')).map(e => e.textContent);
    return sys.some(t => t.indexOf('清空失败') >= 0);
  })()`), 10000, 'clear-fail-msg');
  await sleep(300);
  after = await evalJs(cdp, SNAPSHOT);
  wsClose(cdp, t.id);
  return { label, mockStatus, mode: CHAT_MOCK ? 'offline' : 'real', before, after,
    clearIntercepted: state.clearIntercepted, clearBody: state.clearBody, chatBodies: state.chatBodies };
}

function wsClose(cdp, targetId) {
  try { cdp.ws.close(); } catch { /* ignore */ }
  closeTarget(targetId);
}

/* 精简快照：避免真实回答大 JSON 淹没报告 */
function briefSnap(s) {
  if (!s) return null;
  return {
    msgCount: s.msgCount, user: s.userMsgs, ai: s.aiMsgs, sys: s.sysMsgs,
    ls: s.lsHistory === null ? null : 'len=' + s.lsHistory.length,
    qCookie: s.hasQuestionsCookie, devCookie: s.hasDeviceCookie,
    last: s.lastBubble
  };
}
function briefCase(r) {
  const o = { label: r.label, mode: r.mode };
  if (r.error) { o.error = r.error; return o; }
  o.before = briefSnap(r.before); o.after = briefSnap(r.after);
  if (r.afterReload) o.reload = briefSnap(r.afterReload);
  if (r.thirdAsk) o.thirdBody = r.thirdAsk.body;
  o.clearN = r.clearIntercepted; o.clearBody = r.clearBody;
  if (r.chatBodies) o.chatBodies = r.chatBodies;
  return o;
}

(async () => {
  const skipS3 = process.env.T1_SKIP_S3 === '1';
  const out = [];
  try { out.push(await runCase(200, 'S1-success+S2-reload', { extraThirdAsk: !CHAT_MOCK && CLEAR_REAL })); }
  catch (e) { out.push({ label: 'S1-success+S2-reload', error: e.message }); }
  if (!skipS3) {
    try { out.push(await runCase(500, 'S3-fail')); }
    catch (e) { out.push({ label: 'S3-fail', error: e.message }); }
  }
  process.stdout.write(JSON.stringify(out.map(briefCase), null, 2));
})().catch((e) => { process.stderr.write('FATAL: ' + e.stack + '\n'); process.exit(1); });
