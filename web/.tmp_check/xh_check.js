
(function(){
  'use strict';

  /* ═══════════════════════════════════════════
     1. 运行时 API 基址探测（契约 0.2）
     本地 /xiaohaigpt.html      → API_BASE = ''
     宝塔 /copilot/xiaohaigpt.html → API_BASE = '/copilot'
     ═══════════════════════════════════════════ */
  const API_BASE = (() => {
    const path = location.pathname;
    const m = path.match(/^(\/[^/]*\/)xiaohaigpt\.html$/);
    return m ? m[1].replace(/\/$/, '') : '';
  })();

  /* 把相对路径拼成完整 URL（契约 0.3：已是 http(s) 则原样） */
  function absUrl(p) {
    if (!p) return '';
    if (/^https?:\/\//i.test(p)) return p;
    return API_BASE + (p.charAt(0) === '/' ? p : '/' + p);
  }

  /* ═══════════════════════════════════════════
     2. Cookie 工具（契约 4）
     ═══════════════════════════════════════════ */
  function getCookie(name) {
    const m = document.cookie.match(new RegExp('(?:^|; )' + name.replace(/([.$?*|{}()\[\]\\\/+^])/g, '\\$1') + '=([^;]*)'));
    return m ? decodeURIComponent(m[1]) : '';
  }
  function setCookie(name, value, maxAgeSec) {
    let c = name + '=' + encodeURIComponent(value) + '; path=/; max-age=' + maxAgeSec + '; SameSite=Lax';
    if (location.protocol === 'https:') c += '; Secure';
    document.cookie = c;
  }
  function uuidv4() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c){
      const r = Math.random() * 16 | 0, v = c === 'x' ? r : (r & 0x3 | 0x8);
      return v.toString(16);
    });
  }
  function getDeviceId() {
    let id = getCookie('xh_device_id');
    if (!id) { id = uuidv4(); setCookie('xh_device_id', id, 31536000); }
    return id;
  }
  /* xh_questions：JSON 数组，去重，最多 20 条；编码超 1KB 截断到 10 条（契约 4.2） */
  function getQuestionIds() {
    try { return JSON.parse(getCookie('xh_questions') || '[]'); } catch (e) { return []; }
  }
  function pushQuestionId(qid) {
    let list = getQuestionIds();
    list = [qid, ...list.filter(x => x !== qid)].slice(0, 20);
    let raw = JSON.stringify(list);
    if (raw.length > 1024) list = list.slice(0, 10), raw = JSON.stringify(list);
    setCookie('xh_questions', raw, 31536000);
  }

  /* ═══════════════════════════════════════════
     3. 聊天历史（localStorage 兜底，契约 4.1）
     结构：[{role, content, time, ...extra}]，最多 200 条
     ═══════════════════════════════════════════ */
  const HISTORY_KEY = 'xh_chat_history';
  function loadHistory() {
    try {
      const arr = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
      return Array.isArray(arr) ? arr : [];
    } catch (e) { return []; }
  }
  function saveHistory() {
    try {
      const arr = loadHistory();
      localStorage.setItem(HISTORY_KEY, JSON.stringify(arr.slice(-200)));
    } catch (e) { /* localStorage 不可用时静默 */ }
  }
  function appendHistory(item) {
    try {
      const arr = loadHistory();
      arr.push(item);
      localStorage.setItem(HISTORY_KEY, JSON.stringify(arr.slice(-200)));
    } catch (e) { /* ignore */ }
  }
  /* 用 question_id 更新历史里对应的师哥师姐条目（pending→answered） */
  function updateSeniorHistory(questionId, patch) {
    try {
      const arr = loadHistory();
      const i = arr.findIndex(x => x.kind === 'senior' && x.question_id === questionId);
      if (i >= 0) { Object.assign(arr[i], patch); localStorage.setItem(HISTORY_KEY, JSON.stringify(arr.slice(-200))); }
    } catch (e) { /* ignore */ }
  }

  /* ═══════════════════════════════════════════
     4. DOM 引用
     ═══════════════════════════════════════════ */
  const chatWrap = document.getElementById('chatWrap');
  const inputEl = document.getElementById('input');
  const sendBtn = document.getElementById('sendBtn');
  const hdStatus = document.getElementById('hdStatus');
  const hdStatusTxt = document.getElementById('hdStatusTxt');
  const healthBar = document.getElementById('healthBar');
  const healthTxt = document.getElementById('healthTxt');

  let sending = false;
  let currentSessionId = '';
  /* 已渲染的师哥师姐 question_id（去重，防止轮询重复插入） */
  const renderedSeniorIds = new Set();

  /* ═══════════════════════════════════════════
     5. 消息渲染
     ═══════════════════════════════════════════ */
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function scrollBottom() {
    requestAnimationFrame(() => {
      const main = document.getElementById('chatArea');
      main.scrollTop = main.scrollHeight;
    });
  }

  /* ═══════════════════════════════════════════
     5.1 轻量安全 Markdown 渲染（契约 v1.2）
     安全：先整体 HTML 转义再渲染，只生成白名单标签（strong/em/code/ul/ol/li/h1-6/blockquote/p/br），
           任何原始文本不可能进入标签名或属性。
     兼容：先提取 [Sx] 标号为占位符，渲染完成后再还原成 .ref 徽章，现有引用跳转逻辑不受影响。
     ═══════════════════════════════════════════ */
  function renderAnswerWithRefs(text) {
    const src = String(text == null ? '' : text);
    if (!src) return '';
    const refs = [];
    /* 1) [Sx] 标号 → 占位符 */
    const placeholder = src.replace(/\[(S\d+)\]/g, function(m, id) {
      refs.push(id);
      return '\u0000R' + (refs.length - 1) + '\u0000';
    });
    /* 2) 整体转义（防 XSS） */
    const safe = esc(placeholder);
    /* 3) 行内：行内代码(先保护) → 加粗 → 斜体 → 还原代码 → 还原 [Sx] 徽章 */
    function inline(t) {
      const codes = [];
      let s = t.replace(/`([^`]+)`/g, function(m, c) { codes.push(c); return '\u0000C' + (codes.length - 1) + '\u0000'; });
      s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      s = s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
      s = s.replace(/\u0000C(\d+)\u0000/g, function(m, i) { return '<code>' + codes[+i] + '</code>'; });
      s = s.replace(/\u0000R(\d+)\u0000/g, function(m, i) { return '<button class="ref" data-ref="' + refs[+i] + '">' + refs[+i] + '</button>'; });
      return s;
    }
    /* 4) 块级：空行分段 → 标题/引用/列表/段落 */
    const blocks = safe.split(/\n{2,}/);
    return blocks.map(function(block) {
      const b = block.replace(/^\n+|\n+$/g, '');
      if (!b) return '';
      const h = b.match(/^(#{2,6})\s+(.*)$/);
      if (h) { const lv = Math.min(h[1].length, 6); return '<h' + lv + '>' + inline(h[2]) + '</h' + lv + '>'; }
      if (/^&gt;\s?/.test(b)) {
        const q = b.split('\n').map(function(l) { return l.replace(/^&gt;\s?/, ''); }).join('\n');
        return '<blockquote>' + inline(q) + '</blockquote>';
      }
      if (/^(\-|•|&bull;)\s/.test(b)) {
        const items = b.split('\n')
          .filter(function(l) { return /^(\-|•|&bull;)\s/.test(l); })
          .map(function(l) { return '<li>' + inline(l.replace(/^(\-|•|&bull;)\s/, '')) + '</li>'; });
        return '<ul>' + items.join('') + '</ul>';
      }
      if (/^\d+\.\s/.test(b)) {
        const items = b.split('\n')
          .filter(function(l) { return /^\d+\.\s/.test(l); })
          .map(function(l) { return '<li>' + inline(l.replace(/^\d+\.\s/, '')) + '</li>'; });
        return '<ol>' + items.join('') + '</ol>';
      }
      const para = b.split('\n').map(inline).join('<br>');
      return '<p>' + para + '</p>';
    }).join('');
  }

  /* 引用佐证卡片（契约 3.2.1 渲染规则） */
  const TYPE_ICONS = { pdf:'📄', word:'📝', excel:'📊', image:'🖼️', link:'🔗', qr:'🔳', markdown:'📃', faq:'💬' };
  const AUTHORITY_LABEL = {
    official_policy: '官方政策',
    official_guidance: '官方指南',
    peer_experience: '学生经验',
    resource: '实用资源',
    unverified: '未核实'
  };
  function authorityClass(a) {
    if (a === 'official_policy' || a === 'official_guidance') return 'official';
    if (a === 'peer_experience') return 'peer';
    if (a === 'resource') return 'resource';
    return 'unverified';
  }
  function citationCardHTML(id, c) {
    const type = c.type || '';
    const hasImage = type === 'image' || !!c.image_thumb;
    let body = '';
    // 图片缩略图
    if (hasImage && c.image_thumb) {
      body += '<div class="cite-thumb"><img src="' + esc(absUrl(c.image_thumb)) + '" alt="' + esc(c.title || id) + '" loading="lazy" onerror="this.closest(\'.cite-thumb\').style.display=\'none\'"></div>';
    }
    // 摘要
    if (c.excerpt) body += '<div class="cite-excerpt">' + esc(c.excerpt) + '</div>';
    // 页码（PDF 类）
    if (c.page != null && ['pdf','word','excel'].includes(type)) {
      body += '<div class="cite-page">📄 第 ' + c.page + ' 页</div>';
    }
    // 打开入口：仅对可打开路径生成链接（file_path 以 /files/ /qa/ /qrcodes/ 开头或完整 http(s)），
    // 路径不合理时不渲染"打开"按钮（需求：打开失败或路径不可用则不展示）
    const isOpenablePath = function(p) {
      if (!p) return false;
      if (/^https?:\/\//i.test(p)) return true;
      return p === '/qa' || p.indexOf('/qa/') === 0 || p.indexOf('/files/') === 0 || p.indexOf('/qrcodes/') === 0;
    };
    let href = '';
    if (type === 'link' && c.url) {
      href = absUrl(c.url) || c.url;
    } else if (isOpenablePath(c.file_path)) {
      href = absUrl(c.file_path);
    }
    let openLabel = '';
    let openHref = '';
    if (href) {
      if (type === 'link') openLabel = '↗ 打开链接';
      else if (type === 'qr') openLabel = '🔳 查看';
      else if (type === 'markdown' || type === 'faq') openLabel = '↗ 打开来源';
      else openLabel = '📄 打开文件';
      openHref = href;
    }
    const authLabel = AUTHORITY_LABEL[c.authority] || '来源';
    return (
      '<div class="cite-card" data-id="' + esc(id) + '"' + (openHref ? ' data-href="' + esc(openHref) + '"' : '') + '>' +
        '<div class="cite-head">' +
          '<span class="cite-ico">' + (TYPE_ICONS[type] || '📎') + '</span>' +
          '<span class="cite-name">' + esc(c.title || id) + '</span>' +
          '<span class="cite-authority ' + authorityClass(c.authority) + '">' + esc(authLabel) + '</span>' +
        '</div>' +
        body +
        (openLabel ? '<span class="cite-open">' + openLabel + '</span>' : '') +
      '</div>'
    );
  }
  function citationsHTML(meta) {
    const ids = Object.keys(meta || {});
    if (!ids.length) return '';
    return (
      '<div class="citations">' +
        '<details class="cite-details">' +
          '<summary class="cite-title">📚 引用佐证 · ' + ids.length + ' 条</summary>' +
          '<div class="cite-cards">' +
            ids.map(id => citationCardHTML(id, meta[id])).join('') +
          '</div>' +
        '</details>' +
      '</div>'
    );
  }

  /* 图片佐证卡片区（契约 v1.1 §3.2.2 visual_evidence）
     独立数组，与 citation_metadata（S 系列文本引用）分开渲染；
     无论 answerable 是 true/false，只要有 visual_evidence 就展示。 */
  function visualEvidenceHTML(ve) {
    const list = ve || [];
    if (!list.length) return '';
    const cards = list.map(v => {
      const type = v.type || 'image';
      const isQr = type === 'qr';
      const thumb = v.image_thumb || v.file_path || '';
      const href = thumb || v.url || '';
      const authLabel = AUTHORITY_LABEL[v.authority] || '来源';
      return (
        '<div class="ve-card" data-ve-href="' + esc(absUrl(href)) + '">' +
          '<div class="cite-head">' +
            '<span class="cite-ico">' + (isQr ? '🔳' : '🖼️') + '</span>' +
            '<span class="cite-name">' + esc(v.title || v.evidence_id || '') + '</span>' +
            '<span class="cite-authority ' + authorityClass(v.authority) + '">' + esc(authLabel) + '</span>' +
          '</div>' +
          (isQr ? '<div class="ve-qr-hint">📱 扫码访问/入群</div>' : '') +
          (thumb ?
            '<div class="ve-thumb"><img src="' + esc(absUrl(thumb)) + '" alt="' + esc(v.title || '图片佐证') + '" loading="lazy" ' +
              'onerror="this.parentNode.classList.add(\'ve-img-fail\'); this.style.display=\'none\'"></div>' : '') +
          (v.excerpt ? '<div class="cite-excerpt">' + esc(v.excerpt) + '</div>' : '') +
          '<span class="cite-open">🔍 查看原图</span>' +
        '</div>'
      );
    }).join('');
    return (
      '<div class="visual-evidence">' +
        '<div class="cite-title">🖼️ 相关图片佐证（点击可查看原图）</div>' +
        cards +
      '</div>'
    );
  }

  /* 用户气泡 */
  function addUserMsg(text, meta) {
    const div = document.createElement('div');
    div.className = 'msg user';
    div.innerHTML =
      '<div class="avatar">你</div>' +
      '<div class="msg-body"><div class="bubble">' + esc(text) + '</div></div>';
    chatWrap.appendChild(div);
    scrollBottom();
    if (meta) appendHistory(Object.assign({ role:'user', content:text, time:Date.now() }, meta));
  }

  /* AI 气泡（正常回答 + 引用 + 图片佐证）
     契约 v1.3：source=web_search 时正文上方加"联网搜索"标签，引用区显示 search_results（非官方来源） */
  function addAiMsg(data) {
    const div = document.createElement('div');
    div.className = 'msg ai';
    const isWeb = data.source === 'web_search';
    const hasSearch = isWeb && Array.isArray(data.search_results) && data.search_results.length > 0;
    const tagHTML = isWeb ? '<div class="source-tag">🔍 联网搜索 · 非官方来源</div>' : '';
    /* 联网回答优先展示 search_results；为空则用 citation_metadata 兜底 */
    const citeHTML = hasSearch ? searchResultsHTML(data.search_results) : citationsHTML(data.citation_metadata);
    div.innerHTML =
      '<div class="avatar">🦐</div>' +
      '<div class="msg-body">' +
        tagHTML +
        '<div class="bubble md">' + renderAnswerWithRefs(data.answer) + '</div>' +
        citeHTML +
        visualEvidenceHTML(data.visual_evidence) +
      '</div>';
    chatWrap.appendChild(div);
    scrollBottom();
    appendHistory({
      role:'ai', content:data.answer || '', time:Date.now(),
      kind:'ai', citations:data.citations || [],
      citation_metadata:data.citation_metadata || {},
      visual_evidence:data.visual_evidence || [],
      unknown: !!data.unknown, answerable: !!data.answerable,
      source:data.source || '', search_results:data.search_results || []
    });
  }

  /* 联网搜索结果引用区（契约 v1.3：source=web_search 的 search_results 渲染）
     [{title,url,snippet}] → 轻量卡片：标题 + 可点链接 + 摘要；复用 cite-card 样式与点击委托 */
  function searchResultsHTML(results) {
    const list = results || [];
    if (!list.length) return '';
    const cards = list.map(function(r, i) {
      const url = r.url ? absUrl(r.url) : '';
      const title = r.title || ('搜索结果 ' + (i + 1));
      const snippet = r.snippet || '';
      return (
        '<div class="cite-card" data-id="search-' + (i + 1) + '"' + (url ? ' data-href="' + esc(url) + '"' : '') + '>' +
          '<div class="cite-head">' +
            '<span class="cite-ico">🔍</span>' +
            '<span class="cite-name">' + esc(title) + '</span>' +
            '<span class="cite-authority unverified">网络来源</span>' +
          '</div>' +
          (snippet ? '<div class="cite-excerpt">' + esc(snippet) + '</div>' : '') +
          (url ? '<span class="cite-open">↗ 打开网页</span>' : '') +
        '</div>'
      );
    }).join('');
    return (
      '<div class="citations">' +
        '<details class="cite-details" open>' +
          '<summary class="cite-title">🔍 联网搜索来源 · ' + list.length + ' 条</summary>' +
          '<div class="cite-cards">' + cards + '</div>' +
        '</details>' +
      '</div>'
    );
  }

  /* AI 拒答样式（unknown / !answerable）——拒答卡片下方仍渲染图片佐证（契约 v1.1） */
  function addUnknownMsg(data) {
    const div = document.createElement('div');
    div.className = 'msg ai';
    div.innerHTML =
      '<div class="avatar">🦐</div>' +
      '<div class="msg-body">' +
        '<div class="bubble md">' + renderAnswerWithRefs(data.answer || '') + '</div>' +
        '<div class="unknown-card">' +
          '<div class="u-head"><span class="u-ico">🤔</span><span class="u-title">知识库暂无相关材料</span></div>' +
          '<div class="u-text">' + esc(data.answer || '') + '</div>' +
          '<div class="u-hint">检索到了相关内容，但不足以形成可靠结论，我不编造。</div>' +
          '<button class="u-btn" data-senior-ask="' + esc(data.query || '') + '">试试问师哥师姐 →</button>' +
        '</div>' +
        visualEvidenceHTML(data.visual_evidence) +
      '</div>';
    chatWrap.appendChild(div);
    scrollBottom();
    appendHistory({
      role:'ai', content:data.answer || '', time:Date.now(),
      kind:'ai', unknown:true, answerable:false,
      visual_evidence:data.visual_evidence || [],
      query:data.query || ''
    });
  }

  /* 师哥师姐 pending 占位气泡 */
  function addSeniorPending(qid, question) {
    renderedSeniorIds.add(qid);
    const div = document.createElement('div');
    div.className = 'msg senior';
    div.dataset.qid = qid;
    div.innerHTML =
      '<div class="avatar">🧑‍🎓</div>' +
      '<div class="msg-body">' +
        '<div class="bubble">' +
          '<div class="s-q">' + esc(question) + '</div>' +
          '<div class="s-a">已提交，师哥师姐答复后会显示在这里 ✓</div>' +
          '<div class="s-meta">等待答复中…</div>' +
        '</div>' +
      '</div>';
    chatWrap.appendChild(div);
    scrollBottom();
    appendHistory({
      role:'senior', time:Date.now(), kind:'senior',
      question_id:qid, question:question, status:'pending', reply:null
    });
  }

  /* 师哥师姐 answered 气泡 */
  function addSeniorAnswer(qid, question, reply, nickname) {
    renderedSeniorIds.add(qid);
    const div = document.createElement('div');
    div.className = 'msg senior';
    div.dataset.qid = qid;
    const author = (reply && reply.author) || '师哥师姐';
    const repliedAt = (reply && reply.replied_at) || '';
    let t = '';
    if (repliedAt) { try { t = new Date(repliedAt).toLocaleString('zh-CN', {month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit'}); } catch(e) { t = ''; } }
    div.innerHTML =
      '<div class="avatar">🧑‍🎓</div>' +
      '<div class="msg-body">' +
        '<div class="bubble">' +
          '<div class="s-q">' + esc(question || '') + '</div>' +
          '<div class="s-a">' + esc((reply && reply.text) || '') + '</div>' +
          '<div class="s-meta">' + esc(author) + (t ? ' · ' + t : '') + '</div>' +
        '</div>' +
      '</div>';
    chatWrap.appendChild(div);
    scrollBottom();
    updateSeniorHistory(qid, { status:'answered', reply: reply || {} });
  }

  /* 系统提示气泡 */
  function addSysMsg(text) {
    const div = document.createElement('div');
    div.className = 'msg sys';
    div.innerHTML = '<div class="msg-body"><div class="bubble">' + esc(text) + '</div></div>';
    chatWrap.appendChild(div);
    scrollBottom();
  }

  /* 加载历史（刷新/再访问可见，契约 4.1） */
  function restoreHistory() {
    const arr = loadHistory();
    let lastWasUser = false;
    arr.forEach(h => {
      if (h.kind === 'ai') {
        if (h.unknown) {
          addUnknownMsg({ answer:h.content, query:h.query || '', visual_evidence:h.visual_evidence || [] });
        } else {
          addAiMsg({ answer:h.content, citation_metadata:h.citation_metadata || {}, citations:h.citations || [], visual_evidence:h.visual_evidence || [], source:h.source || '', search_results:h.search_results || [] });
        }
        lastWasUser = false;
      } else if (h.kind === 'senior') {
        if (h.status === 'answered' && h.reply) {
          addSeniorAnswer(h.question_id, h.question, h.reply);
        } else if (h.status === 'pending') {
          addSeniorPending(h.question_id, h.question);
        }
      } else {
        addUserMsg(h.content || '');
        lastWasUser = true;
      }
    });
    /* 若历史以用户问题结尾（无回答），提示可补充 */
    if (lastWasUser) addSysMsg('上次问题似乎没有收到回答，可以重新发一次。');
  }

  /* ═══════════════════════════════════════════
     6. 问答逻辑
     ═══════════════════════════════════════════ */
  async function ask(query) {
    if (sending || !query) return;
    sending = true; sendBtn.disabled = true;
    addUserMsg(query);
    inputEl.value = '';
    autoGrow();

    /* 打字指示器 */
    const typing = document.createElement('div');
    typing.className = 'msg ai';
    typing.innerHTML = '<div class="avatar">🦐</div><div class="msg-body"><div class="bubble"><span class="typing"><i></i><i></i><i></i></span></div></div>';
    chatWrap.appendChild(typing);
    scrollBottom();

    try {
      const body = { query: query, top_k: 6 };
      if (currentSessionId) body.session_id = currentSessionId;
      const res = await fetch(API_BASE + '/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      const data = await res.json();
      typing.remove();
      if (!res.ok) {
        const detail = (data && data.detail) || data.error || ('HTTP ' + res.status);
        addSysMsg('请求失败：' + detail);
        return;
      }
      /* 模型状态：用本次回答实际 composer 顺带刷新（比轮询更准确） */
      if (data.composer) refreshModelFromComposer(data.composer);
      if (data.session_id) currentSessionId = data.session_id;
      /* 契约 v1.3：无论 answerable true/false，回答渲染后都挂"问师哥师姐"入口 */
      if (data.unknown === true || data.answerable === false) {
        addUnknownMsg(data);  // 拒答样式自带"试试问师哥师姐"按钮
      } else {
        addAiMsg(data);
      }
      /* 软拒答文案里"点击下方「问师哥师姐」"有按钮可点（契约 3.6 + v1.3） */
      attachSeniorCta(data.query || query);
    } catch (err) {
      typing.remove();
      addSysMsg('连接失败，请确认后端服务已启动（本地 8000 / 线上反代 8010）。');
    } finally {
      sending = false; sendBtn.disabled = false;
    }
  }

  /* "问题没有解决？问师哥师姐 →" 按钮 */
  function attachSeniorCta(question) {
    const wrap = document.createElement('div');
    wrap.className = 'senior-cta';
    wrap.innerHTML = '<button data-senior-ask="' + esc(question) + '">问题没有解决？问师哥师姐 →</button>';
    chatWrap.appendChild(wrap);
    scrollBottom();
  }

  /* 提交师哥师姐（契约 3.6 + 4.2） */
  async function askSenior(question) {
    if (!question) return;
    const device_id = getDeviceId();
    /* 防重复提交：同一问题 10s 内 */
    const now = Date.now();
    if (window.__lastSeniorAsk && window.__lastSeniorAsk.q === question && now - window.__lastSeniorAsk.t < 10000) return;
    window.__lastSeniorAsk = { q: question, t: now };
    try {
      const res = await fetch(API_BASE + '/api/senior/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_id: device_id, question: question, nickname: '匿名' })
      });
      const data = await res.json();
      if (!res.ok) {
        addSysMsg('提交失败：' + ((data && data.detail) || data.error || res.status));
        return;
      }
      const qid = data.question_id;
      pushQuestionId(qid);                 // 写入 xh_questions cookie
      addSeniorPending(qid, question);     // pending 占位
      /* 去掉可能已存在的同问题 CTA 按钮，避免重复 */
      document.querySelectorAll('.senior-cta').forEach(b => { if (b.querySelector('button') && b.querySelector('button').dataset.seniorAsk === question) b.remove(); });
    } catch (err) {
      addSysMsg('提交失败：无法连接后端服务。');
    }
  }

  /* ═══════════════════════════════════════════
     7. 师哥师姐轮询（契约 3.7，每 30s）
     ═══════════════════════════════════════════ */
  let polling = false;
  async function pollSeniorAnswers() {
    if (polling) return;
    polling = true;
    const device_id = getDeviceId();
    try {
      const res = await fetch(API_BASE + '/api/senior/answers?device_id=' + encodeURIComponent(device_id));
      if (!res.ok) return;
      const data = await res.json();
      const qs = (data && data.questions) || [];
      /* 按创建时间正序（后端倒序，这里倒过来处理，先渲染旧的） */
      [...qs].reverse().forEach(q => {
        if (renderedSeniorIds.has(q.question_id)) {
          /* 已有 pending 占位，若已答复则升级 */
          if (q.status === 'answered' && q.reply) {
            const existing = chatWrap.querySelector('.msg.senior[data-qid="' + q.question_id + '"]');
            if (existing && !existing.dataset.answered) {
              existing.dataset.answered = '1';
              existing.querySelector('.bubble').innerHTML =
                '<div class="s-q">' + esc(q.question) + '</div>' +
                '<div class="s-a">' + esc((q.reply && q.reply.text) || '') + '</div>' +
                '<div class="s-meta">' + esc((q.reply && q.reply.author) || '师哥师姐') + '</div>';
              updateSeniorHistory(q.question_id, { status:'answered', reply:q.reply });
            }
          }
          return;
        }
        if (q.status === 'answered' && q.reply) {
          addSeniorAnswer(q.question_id, q.question, q.reply, q.nickname);
        } else {
          /* 本地历史可能已有该 pending，避免重复 */
          const inHistory = loadHistory().some(h => h.kind === 'senior' && h.question_id === q.question_id);
          if (!inHistory) addSeniorPending(q.question_id, q.question);
          else renderedSeniorIds.add(q.question_id);
        }
      });
    } catch (e) { /* 网络波动忽略 */ }
    finally { polling = false; }
  }

  /* ═══════════════════════════════════════════
     8. 模型状态指示灯（/api/model/check 探活）
        绿=模型在线 · 黄=本地降级 · 红=调用失败/服务不可达
     ═══════════════════════════════════════════ */
  const MODEL_STATES = {
    ok:   { hb:'ok',   hd:'ok',   hdTxt:'在线',     hdTitle:'模型在线' },
    warn: { hb:'warn', hd:'warn', hdTxt:'降级回答', hdTitle:'模型未配置，本地降级回答中' },
    err:  { hb:'err',  hd:'err',  hdTxt:'模型异常', hdTitle:'模型调用失败' },
    down: { hb:'err',  hd:'err',  hdTxt:'离线',     hdTitle:'后端服务未连接' }
  };
  let lastModelLabel = '';

  function setModelStatus(state, hbText, detail) {
    const s = MODEL_STATES[state] || MODEL_STATES.down;
    const tip = detail || s.hdTitle;
    healthBar.className = 'health-bar ' + s.hb;
    healthTxt.textContent = hbText;
    healthTxt.title = tip;
    hdStatus.className = 'hd-status ' + s.hd;
    hdStatusTxt.textContent = s.hdTxt;
    hdStatus.title = tip;
  }

  /* 由 /api/model/check 响应计算三态 */
  function applyModelCheck(data) {
    if (data && data.ok === true) {
      const parts = [];
      if (data.provider) parts.push(data.provider);
      if (data.model) parts.push(data.model);
      lastModelLabel = parts.join(' · ');
      setModelStatus('ok', '模型在线' + (lastModelLabel ? ' · ' + lastModelLabel : ''), data.detail || 'model_ok');
      return;
    }
    if (data && data.composer === 'extractive_fallback') {
      setModelStatus('warn', '模型未配置，本地降级回答中', data.detail || 'missing_api_key');
      return;
    }
    setModelStatus('err', '模型调用失败', (data && data.detail) || 'model_error');
  }

  /* 探活一次（8s 超时，AbortController） */
  async function checkModelHealth() {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 8000);
    try {
      const res = await fetch(API_BASE + '/api/model/check', { signal: ctrl.signal, cache: 'no-store' });
      const data = await res.json().catch(() => null);
      if (!res.ok) { setModelStatus('err', '模型调用失败', 'HTTP ' + res.status); return; }
      applyModelCheck(data);
    } catch (e) {
      if (e && e.name === 'AbortError') setModelStatus('down', '后端服务未连接', '探活超时（8s）');
      else setModelStatus('down', '后端服务未连接', '无法连接后端服务');
    } finally {
      clearTimeout(timer);
    }
  }

  /* 聊天响应里的 composer 顺带刷新（不额外发请求，更准确） */
  function refreshModelFromComposer(composer) {
    if (composer === 'openai_compatible') {
      setModelStatus('ok', '模型在线' + (lastModelLabel ? ' · ' + lastModelLabel : ''), '本次回答由模型生成');
    } else if (composer === 'extractive_fallback') {
      setModelStatus('warn', '模型未配置，本地降级回答中', '本次回答走了本地抽取降级');
    }
    /* 其他 composer 不覆盖，等下一轮探活校正 */
  }

  /* ═══════════════════════════════════════════
     9. 事件绑定
     ═══════════════════════════════════════════ */
  function autoGrow() {
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
  }
  inputEl.addEventListener('input', () => {
    autoGrow();
    sendBtn.disabled = sending || !inputEl.value.trim();
  });
  inputEl.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (!sending && inputEl.value.trim()) ask(inputEl.value.trim());
    }
  });
  sendBtn.addEventListener('click', () => {
    if (!sending && inputEl.value.trim()) ask(inputEl.value.trim());
  });
  /* 事件委托：chip / [Sx] 引用跳转 / 引用卡片点击 / 问师哥师姐 */
  chatWrap.addEventListener('click', e => {
    const chip = e.target.closest('.chip');
    if (chip) { ask(chip.dataset.q); return; }
    const ref = e.target.closest('.ref');
    if (ref) {
      const id = ref.dataset.ref;
      const card = chatWrap.querySelector('.cite-card[data-id="' + id + '"]');
      if (card) {
        /* 展开引用区（若收起）→ 滚动定位 → flash 高亮，并打开对应来源文件 */
        const details = card.closest('.cite-details');
        if (details && !details.open) details.open = true;
        card.scrollIntoView({ behavior:'smooth', block:'center' });
        card.classList.remove('flash'); void card.offsetWidth; card.classList.add('flash');
        const href = card.dataset.href;
        if (href) { window.open(href, '_blank', 'noopener,noreferrer'); }
      }
      return;
    }
    const card = e.target.closest('.cite-card');
    if (card) {
      const href = card.dataset.href;
      if (href) { window.open(href, '_blank', 'noopener,noreferrer'); }
      return;
    }
    /* 图片佐证卡片：点击新窗口打开原图（契约 v1.1 §3.2.2） */
    const veCard = e.target.closest('.ve-card');
    if (veCard) {
      const href = veCard.dataset.veHref;
      if (href) { window.open(href, '_blank', 'noopener,noreferrer'); }
      return;
    }
    const askBtn = e.target.closest('[data-senior-ask]');
    if (askBtn) { askSenior(askBtn.dataset.seniorAsk || ''); return; }
  });
  /* 把 [Sx] 引用跳转保留（见 .ref 分支） */

  /* ═══════════════════════════════════════════
     10. 初始化
     ═══════════════════════════════════════════ */
  function init() {
    /* 确保 device_id 已生成（契约 4.2 规则 1） */
    getDeviceId();
    /* 恢复历史（含师哥师姐答复） */
    restoreHistory();
    /* 拉取该设备的提问/答复（进入页面即拉一次，契约 4.2 规则 3） */
    pollSeniorAnswers();
    setInterval(pollSeniorAnswers, 30000);
    /* 模型状态指示灯：进页探活一次 + 60s 轮询 */
    checkModelHealth();
    setInterval(checkModelHealth, 60000);
    /* 默认禁用发送按钮直到有输入 */
    sendBtn.disabled = true;
    scrollBottom();
  }
  init();
})();
