# XiaohaiGPT 后端 API 契约 v1.3

> 版本：v1.3（v1.2 + 未知问题联网搜索/软拒答 + 拒答不再回退 fallback + 根目录百问百答语料入库）
> 面向：虾虾（前端适配）、云云（联调验收）、PHP 站集成
> 最后更新：2026-08-03

---

## 0. 服务模式与访问路径

后端是**独立 Python 服务**（零第三方核心依赖，解析器为可选 extras），前端是**静态页面**（由后端直接 serve，或由宝塔 PHP 站承载后通过反代访问）。

### 0.1 两种运行模式

| 模式 | API 基址 | 静态页 | 说明 |
|------|----------|--------|------|
| 本地开发 | `http://127.0.0.1:8000` | `http://127.0.0.1:8000/xiaohaigpt.html` | `start.bat` / `start.sh` 一键启动，前后端同源 |
| 宝塔线上 | `https://58.87.99.155/copilot/` | `https://58.87.99.155/copilot/xiaohaigpt.html` | Nginx 反代 `/copilot/` → `127.0.0.1:8010`，与 PHP 站同源 |

### 0.2 前端如何调用 API（关键约定）

**推荐方案：同源相对路径 + 运行时探测。** 前端不要写死基址，统一用：

```js
// 运行时自动探测 API 基址（覆盖两种模式）
const API_BASE = (() => {
  const path = location.pathname;
  // 宝塔反代下页面路径形如 /copilot/xiaohaigpt.html
  const m = path.match(/^(\/[^/]*\/)xiaohaigpt\.html$/);
  return m ? m[1].replace(/\/$/, '') : '';   // 同源前缀，无则空串
})();
// 之后所有请求：fetch(API_BASE + '/api/chat')
```

- **本地模式**：`location.pathname === '/xiaohaigpt.html'` → `API_BASE = ''` → 请求 `/api/chat`
- **宝塔模式**：`location.pathname === '/copilot/xiaohaigpt.html'` → `API_BASE = '/copilot'` → 请求 `/copilot/api/chat`
- 后端**同时**返回宽松 CORS 头（`Access-Control-Allow-Origin: *`），即使前端在别的域名/端口（例如先直接打开 `file://` 或用 PHP 站 8000 之外的页面）也能跨域调用。但**强烈建议同源**，避免跨域 Cookie 问题（师哥师姐功能依赖 cookie 里的 device_id）。

### 0.3 静态资源映射

| 前缀 | 映射到 | 用途 |
|------|--------|------|
| `{API_BASE}/xiaohaigpt.html` | 后端 web/ 目录 | 主页面（虾虾交付） |
| `{API_BASE}/static/*` | 后端 web/static/ | 前端 css/js/img |
| `{API_BASE}/files/*` | 站点 `files/` 目录 | 佐证文件（PDF/Excel/图片）；**pdf 以 inline 预览打开**，其余下载 |
| `{API_BASE}/qrcodes/*` | 站点 `qrcodes/` 目录 | 二维码图片佐证 |
| `{API_BASE}/qa/*` | 站点 `qa/` 目录 | FAQ/markdown 引用（**v1.2 新增**，md 以 text/markdown 打开） |
| `{API_BASE}/corpus/*` | 站点根目录 md | 根目录问答语料引用（**v1.3 新增**，如 `/corpus/新生问答整理.md`，md 以 text/markdown 打开） |

> citation 元数据里的 `image_thumb` / `file_path` / `url` 返回的都是**相对于 API 基址的路径**（如 `/files/GPA折算表.jpg`、`/qa/新生常见问题.md`），前端渲染时统一拼成 `API_BASE + path`。若字段已是完整 `http(s)://` 则直接使用。
>
> **引用路径规则（v1.2）**：
> - `pdf/word/excel/image` → `file_path` 为 `/files/...`（pdf 后端返回 inline，浏览器新标签即预览）。
> - `markdown/faq` → `file_path` 为 `/qa/xxx.md`（v1.2 起后端已补 `/qa/` 映射，可直接打开）；根目录「新生问答整理.md」的引用为 `/corpus/新生问答整理.md`（v1.3 起后端已补 `/corpus/` 映射）。
> - `link` → 使用 `url`（完整外链），前端 `target=_blank` 跳转。
> - `qr` → `file_path` 为 `/qrcodes/...`。
> - 若某条引用拿不到可访问的 `file_path`/`url`（值为 null/空），前端**不渲染"打开"按钮**，只展示标题与摘要卡片。

---

## 1. 通用约定

- 编码：UTF-8，请求/响应均为 JSON（`Content-Type: application/json`）。
- 错误统一结构：`{"error": "错误码", "detail": "可读说明"}`。
- 时间字段：ISO 8601 字符串（本地时区）。
- `profile` 字段（各接口通用）：`{"cohort": "2026", "major": "xxx", "student_level": "undergraduate|graduate|all"}`，全部可选。
- 请求体上限 64KB；query 最长 500 字。

---

## 2. 接口清单总览

| # | 方法 | 路径 | 说明 |
|---|------|------|------|
| 1 | GET | `/api/health` | 服务/模型/语料状态 |
| 2 | POST | `/api/chat` | 问答（核心） |
| 3 | POST | `/api/search` | 原始检索（调试/前端展示用） |
| 4 | POST | `/api/context` | 模型证据包（含 can_generate） |
| 5 | POST | `/api/ingest` | 触发即时增量索引 |
| 6 | POST | `/api/senior/ask` | 问师哥师姐（学生侧） |
| 7 | GET | `/api/senior/answers` | 查自己的提问+答复（学生侧） |
| 8 | POST | `/api/senior/reply` | 师哥师姐入答（管理侧） |
| 9 | GET | `/api/senior/pending` | 待答复列表（管理侧） |
| 10 | GET | `/api/corpus/stats` | 语料统计 |
| 11 | POST | `/api/session/reset` | 重置会话（可选） |

---

## 3. 接口详述

### 3.1 GET `/api/health`

服务健康检查。前端页面加载时可调用一次，决定是否展示"知识库就绪"提示。

**响应 200**

```json
{
  "status": "ok",
  "server_time": "2026-07-31T18:00:00+08:00",
  "database_ready": true,
  "composer": {
    "mode": "fallback",
    "adapter": "extractive_fallback",
    "provider": "custom",
    "model": null,
    "base_url": null,
    "credential_configured": false,
    "reason": "model_not_configured"
  },
  "corpus": {
    "site_root": "D:/CUC_Files/26新生网站",
    "files_json_exists": true,
    "files_dir_exists": true,
    "qa_dir_exists": true,
    "qrcodes_json_exists": true,
    "documents": 26,
    "chunks": 120,
    "last_build_at": "2026-07-31T17:59:59+08:00",
    "degraded": false,
    "degraded_reason": null
  },
  "rag": {
    "auto_poll_seconds": 60,
    "incremental": true
  }
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | `ok` / `degraded` / `error` |
| database_ready | bool | SQLite 索引是否已建 |
| composer.mode | string | `model`（走 LLM）/ `fallback`（本地抽取式） |
| composer.credential_configured | bool | 是否配置了 API Key |
| corpus.site_root | string | 自动探测到的站点根目录 |
| corpus.documents / chunks | int | 索引文档数 / 分块数（未建为 0） |
| corpus.degraded | bool | 是否降级运行（缺解析器/缺文件） |
| rag.auto_poll_seconds | int | 自动轮询间隔，0 表示关闭 |

**错误码**：无（始终 200；异常时 status=error）。

---

### 3.2 POST `/api/chat`（核心问答）

**请求体**

```json
{
  "query": "宿舍是几人间？",
  "profile": {"cohort": "2026", "student_level": "undergraduate"},
  "session_id": "abc123",
  "top_k": 6
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| query | string | 是 | 用户问题，≤500 字 |
| profile | object | 否 | 见通用约定 |
| session_id | string | 否 | 多轮会话 id（前端可不传，服务端生成） |
| top_k | int | 否 | 1~10，默认 6 |

**响应 200（可生成）**

```json
{
  "session_id": "a1b2c3...",
  "query": "宿舍是几人间？",
  "retrieval_query": "宿舍是几人间？",
  "answer": "**结论**：海南国际学院的宿舍是四人间、上床下桌、独立卫浴，有空调和热水器。[S1]\n\n**分点说明**：\n1. 学生经验（不是学校官方规定）《新生常见问题问答库》宿舍为四人间，上床下桌，独立卫浴，有空调和热水器，每层有公共洗衣房。[S1]\n2. 现有学校材料《中国传媒大学学生手册》宿舍实行统一住宿管理，学生应按分配房间入住。[S2]\n3. 学生经验（不是学校官方规定）《家具尺寸统计表》生活一区床垫尺寸 2m×1m。[S3]",
  "answerable": true,
  "unknown": false,
  "answerability": "supported_with_context",
  "insufficient_reason": null,
  "citations": ["S1", "S2", "S3"],
  "citation_metadata": {
    "S1": {
      "evidence_id": "S1",
      "title": "新生常见问题问答库",
      "file_path": "/qa/新生常见问题.md",
      "type": "markdown",
      "image_thumb": null,
      "url": null,
      "page": null,
      "authority": "peer_experience",
      "assertion_policy": "label_as_experience",
      "chunk_type": "faq",
      "excerpt": "问题：宿舍条件怎么样？回答：海南国际学院的宿舍是四人间……"
    },
    "S2": {
      "evidence_id": "S2",
      "title": "中国传媒大学学生手册.pdf",
      "file_path": "/files/中国传媒大学学生手册.pdf",
      "type": "pdf",
      "image_thumb": null,
      "url": null,
      "page": 12,
      "authority": "official_guidance",
      "assertion_policy": "assert_if_current",
      "chunk_type": "document",
      "excerpt": "学生宿舍实行统一住宿管理……"
    },
    "S3": {
      "evidence_id": "S3",
      "title": "生活一、二区家具尺寸统计表.xlsx",
      "file_path": "/files/生活一、二区家具尺寸统计表.xlsx",
      "type": "excel",
      "image_thumb": null,
      "url": null,
      "page": null,
      "authority": "peer_experience",
      "assertion_policy": "label_as_experience",
      "chunk_type": "structured_fact",
      "excerpt": "生活一区 床垫 2m×1m"
    }
  },
  "claims": [
    {"text": "宿舍为四人间", "evidence_ids": ["S1"], "certainty": "official"}
  ],
  "unresolved": ["《学生手册》的生效时间尚未核实"],
  "composer": "extractive_fallback",
  "composer_warning": null,
  "evidence": [ { "evidence_id": "S1", "title": "...", "text": "...", "...": "完整证据对象，见 3.2.1" } ],
  "visual_evidence": [
    {
      "evidence_id": "V1",
      "title": "试验区摆渡车线路图-2026年3月版.png",
      "file_path": "/files/试验区摆渡车线路图-2026年3月版.png",
      "type": "image",
      "image_thumb": "/files/试验区摆渡车线路图-2026年3月版.png",
      "url": null,
      "authority": "unverified",
      "assertion_policy": "do_not_assert_until_ocr",
      "chunk_type": "visual_reference",
      "excerpt": "试验区摆渡车线路图-2026年3月版.png\n试验区摆渡车线路及班次（2026年3月版）"
    }
  ],
  "conversation_turns": 1
}
```

**响应 200（未知/拒答）——重要**

```json
{
  "session_id": "a1b2c3...",
  "query": "学校游泳池水深多少米？",
  "retrieval_query": "学校游泳池水深多少米？",
  "answer": "知识库里暂时没有找到关于「学校游泳池水深多少米」的可靠材料。你可以① 点击下方「问师哥师姐」，让学长学姐给你权威解答；② 换个问法试试。以学校最新通知为准。",
  "answerable": false,
  "unknown": true,
  "answerability": "insufficient",
  "insufficient_reason": "top1_score_below_threshold",
  "citations": [],
  "citation_metadata": {},
  "claims": [],
  "unresolved": ["缺少能够支持结论的有效来源"],
  "composer": "openai_compatible",
  "composer_warning": null,
  "evidence": [],
  "visual_evidence": [],
  "conversation_turns": 1,
  "source": "refusal",
  "search_results": null
}
```

**响应 200（未知 + 联网搜索命中，v1.3 新增）**

```json
{
  "session_id": "a1b2c3...",
  "query": "食堂麻辣香锅多少钱",
  "answer": "**联网搜索结果**（关于「食堂麻辣香锅多少钱」）：\n1. **xxx**：麻辣香锅按重量计价…… [W1]\n   来源：https://…\n\n以上结果来自互联网搜索，并非学校官方信息，仅供参考。你可以点击下方「问师哥师姐」……以学校最新通知为准。",
  "answerable": false,
  "unknown": true,
  "citations": ["W1", "W2"],
  "citation_metadata": {
    "W1": {"evidence_id": "W1", "title": "…", "file_path": null, "type": "link", "url": "https://…", "authority": "web_search", "chunk_type": "web_search", "excerpt": "…"}
  },
  "composer": "openai_compatible",
  "composer_warning": null,
  "source": "web_search",
  "search_results": [{"title": "…", "link": "https://…", "snippet": "…"}]
}
```

> **前端判据（v1.3）**：`unknown === true` 时展示拒答卡片（"知识库未找到相关材料"），不要展示**文本佐证卡片**，也不要让用户误以为答了。
> - `source === "web_search"`：answer 是联网搜索结果（非官方、仅供参考），前端渲染 **W 系列引用卡片**（type=link，可点开外链），并保留「问师哥师姐」引导。
> - `source === "refusal"`：answer 是软拒答文案（含「问师哥师姐」引导），前端照常渲染拒答卡片 + 「问师哥师姐」按钮。
> - `source === "corpus"`：语料回答（`answerable === true` 时默认）。
> `answerable === true` 时正常渲染 answer + 佐证卡片。
> **answer 格式（v1.2）**：`answer` 是 **markdown 正文**，统一为「`**结论**：…`（加粗）→ `**分点说明**`（1. 2. 3. 列表）→ 每条后带 `[Sx]` 引用标号」。fallback（无 API Key）与 LLM 模式都遵循此格式，前端按 markdown 渲染。
> **多来源引用（v1.2）**：检索组装按「文档多样性优先」——不同文档的 chunk 优先入选（同文档最多 1~2 条），因此 `citations`/`citation_metadata` 可能包含 S1/S2/S3… 多个不同来源。`citation_metadata` 始终覆盖全部被引用来源（不只 S1）。
> **图片佐证（v1.1 新增）**：即使 `unknown === true`，`visual_evidence` 仍可能非空——此时在拒答卡片下方渲染"**相关图片佐证**"卡片区（`<img src="{API_BASE}{image_thumb}">`），引导用户看图。图片佐证**永远不改变 answerable 状态**：只有图片命中时 answer 仍是拒答，但图片卡片照常展示。
> **拒答语义（v1.3）**：未知问题（检索判定不可生成）默认走 **LLM 合规拒答**（composer=openai_compatible、warning=null、answerable=false、软拒答文案），**不再回退 extractive_fallback**。若配置了 `SEARCH_API_KEY`，会先尝试联网搜索，命中则返回 `source=web_search` 的联网回答（不硬性拒答）。
> **拒答引用清理**：拒答/联网场景下 `citations` 与 `citation_metadata` 只含联网 W 系列（web_search），语料 S 系列一律清空；`claims` 为空。

**3.2.1 citation 元数据字段（前端渲染佐证卡片用）**

| 字段 | 类型 | 说明 |
|------|------|------|
| evidence_id | string | 引用标号 `S1`/`S2`…（联网为 `W1`/`W2`…） |
| title | string | 文档/链接/二维码标题 |
| file_path | string\|null | 相对文件路径（`/files/xxx.pdf` 或 `/qrcodes/xxx.jpg`） |
| type | string | `pdf` / `word` / `excel` / `image` / `link` / `qr` / `markdown` / `faq` |
| image_thumb | string\|null | 图片类佐证的缩略图路径（`/qrcodes/xxx.jpg`、`/files/xxx.png`）；非图片为 null |
| url | string\|null | 链接类的外链地址 |
| page | int\|null | 引用页码（PDF 类） |
| authority | string | `official_policy` / `official_guidance` / `peer_experience` / `resource` / `unverified` / `web_search`（v1.3 联网引用） |
| assertion_policy | string | `assert_with_citation` / `assert_if_current` / `label_as_experience` / `navigation_only` / `do_not_assert` |
| chunk_type | string | `document` / `faq` / `structured_fact` / `resource` / `qr_entry` |
| excerpt | string | 命中片段摘要（≤300 字，渲染卡片副文本） |

**前端渲染规则（虾虾）**
- `type` 为 `image` 或存在 `image_thumb`：直接 `<img src="{API_BASE}{image_thumb}">` 展示。
- `type` 为 `pdf/word/excel`：卡片显示文件名 + 页码 + 打开按钮（`href={API_BASE}{file_path}`；pdf 后端返回 inline 预览，word/excel 为下载）。
- `type` 为 `link`：卡片显示标题 + 外链图标（`href={url}`，`target=_blank`）。
- `type` 为 `markdown/faq`（**v1.2**）：可点击打开 `/qa/xxx.md`（后端已配 `/qa/` 映射），`clickableTypes` 需加入 `markdown`/`faq`。
- `type` 为 `qr`：显示二维码标题 + 图片 + 描述。
- **通用规则（v1.2）**：仅当 `file_path`（或 link 的 `url`）非空且可访问时才渲染"打开"按钮；字段为 null/空时只展示标题/摘要卡片。
- `answer` 为 **markdown 正文**（`**结论**：…` + `**分点说明**` 列表 + `[Sx]`），前端需用 md 渲染（把 `[Sx]` 渲染成可点击引用徽章，点击滚动到对应佐证卡片）。
- authority 前缀样式建议：official=蓝、peer=橙、unverified=灰。

**3.2.2 `visual_evidence` 图片佐证字段（v1.1 新增）**

| 字段 | 类型 | 说明 |
|------|------|------|
| evidence_id | string | 佐证编号 `V1`/`V2`…（与 S 系列文本引用区分） |
| title | string | 图片文件名（如 `GPA折算表.jpg`） |
| file_path | string | 图片相对路径（`/files/xxx.jpg` 或 `/qrcodes/xxx.jpg`） |
| type | string | `image`（图片佐证）或 `qr`（二维码） |
| image_thumb | string | **原图 URL**（`{API_BASE}{image_thumb}` 直接 `<img>` 展示） |
| url | string\|null | 二维码/链接外链 |
| authority | string | 图片多为 `unverified`（未 OCR，不断言事实） |
| assertion_policy | string | `do_not_assert_until_ocr`（图片不可作为文本断言） |
| chunk_type | string | `visual_reference`（图片）或 `resource`（二维码） |
| excerpt | string | 图片标题/描述摘要（≤200 字，作卡片副文本） |

**前端渲染规则（虾虾）**
- `visual_evidence` 是独立数组，与 `citation_metadata`（S 系列文本引用）**分开渲染**。
- 无论 `answerable` 是 true 还是 false，只要有 `visual_evidence` 就渲染"**相关图片佐证**"卡片区：`<img src="{API_BASE}{image_thumb}" loading="lazy">`，点击放大/新窗口打开。
- 图片佐证**不代表已回答**：若 `unknown === true`，主区显示拒答文案，图片区在下方独立展示（"相关图片佐证"引导用户看图）。
- `type === 'qr'` 时显示"扫码入群/扫码访问"提示。

**错误码**

| 错误码 | HTTP | 说明 |
|--------|------|------|
| query_required | 400 | query 为空 |
| query_too_long | 400 | 超 500 字 |
| request_body_too_large | 400 | 请求体超 64KB |
| invalid_request | 400 | JSON 解析失败等 |
| request_failed | 500 | 服务端异常 |

---

### 3.3 POST `/api/search`（原始检索，调试用）

**请求体**：同 `/api/chat`（query / profile / top_k）。

**响应 200**

```json
{
  "query": "宿舍是几人间？",
  "intent": "campus_life",
  "retrieval_mode": "fts5_plus_local_subword_rrf",
  "answerability": "supported",
  "requires_uncertainty_label": false,
  "results": [
    {
      "chunk_id": "chunk-xxx",
      "document_id": "doc-xxx",
      "title": "学生手册.pdf",
      "text": "……",
      "chunk_type": "document",
      "heading_path": "",
      "page_number": 12,
      "tags": ["宿舍"],
      "authority_tier": "official_guidance",
      "assertion_policy": "assert_if_current",
      "source_url": "https://...",
      "file_path": "/files/学生手册.pdf",
      "media_type": "pdf",
      "image_thumb": null,
      "cohort": null,
      "academic_year": null,
      "student_level": "all",
      "major": null,
      "campus": "CUCHIC",
      "score": 0.0832,
      "score_explanation": { "relevance_score": 0.0832, "concept_coverage": 0.5, "...": "..." }
    }
  ],
  "visual_evidence": [
    {
      "title": "GPA折算表.jpg",
      "media_type": "image",
      "file_path": "/files/GPA折算表.jpg",
      "image_thumb": "/files/GPA折算表.jpg",
      "chunk_type": "visual_reference",
      "score": 0.1160
    }
  ]
}
```

> `results[].file_path` / `media_type` / `image_thumb` 是本次新增字段，供前端调试与佐证。
> `visual_evidence`（v1.1）：图片/二维码佐证独立数组。**图片块不出现在 `results` 中**（不参与 answerability 判定、不占文本证据位），单独在 `visual_evidence` 返回。

---

### 3.4 POST `/api/context`（模型证据包）

**请求体**：同 `/api/chat`。

**响应 200**

```json
{
  "query": "宿舍是几人间？",
  "conversation_history": [],
  "can_generate": true,
  "response_mode": "supported",
  "insufficient_reason": null,
  "system_rules": ["Only use supplied evidence; ..."],
  "evidence": [ { "evidence_id": "S1", "title": "...", "...": "含 file_path/image_thumb/url/page/authority 的完整对象" } ],
  "model_contract_version": "campus-grounding-v2"
}
```

> 前端一般不直接用；供高级调试 / 接入外部 LLM 编排。

---

### 3.5 POST `/api/ingest`（即时增量索引）

**用途**：PHP 站每次上传/更新文件/链接/二维码后，调用此接口触发**增量重建**——只重切 checksum 变化的文档，不动未变化文档。幂等，可重复调用。

**请求体**（可为空 `{}` 或 `null`）

```json
{ "force": false }
```

| 字段 | 类型 | 说明 |
|------|------|------|
| force | bool | 默认 false：只重建变更文档；true：全量重建 |

**响应 200**

```json
{
  "status": "ok",
  "mode": "incremental",
  "changed": 2,
  "added": 1,
  "removed": 0,
  "unchanged": 23,
  "documents": 26,
  "chunks": 122,
  "build_ms": 456,
  "degraded": false,
  "details": [
    {"document_id": "doc-xxx", "title": "入学指南.pdf", "action": "changed", "parse_status": "parsed"},
    {"document_id": "doc-yyy", "title": "新文件.pdf", "action": "added", "parse_status": "parsed"}
  ]
}
```

**错误码**

| 错误码 | HTTP | 说明 |
|--------|------|------|
| site_root_not_found | 500 | 站点根目录探测失败 |
| rebuild_failed | 500 | 重建异常 |

> **PHP 站集成**：在 `api.php` 上传/删除成功后 `file_get_contents('http://127.0.0.1:8010/api/ingest', ...)` 触发即可（PHP 侧仅新增这一行调用，不改现有逻辑；契约定稿后由云云确认是否由虾虾/运维加）。

---

### 3.6 POST `/api/senior/ask`（问师哥师姐-学生侧）

**请求体**

```json
{
  "device_id": "e3f2a1b0-...",
  "question": "大一新生需要提前买教材吗？",
  "nickname": "匿名"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| device_id | string | 是 | 前端 cookie 中的 `xh_device_id`（UUID） |
| question | string | 是 | 问题正文，≤500 字 |
| nickname | string | 否 | 匿名昵称，默认"匿名" |

**响应 200**

```json
{
  "status": "ok",
  "question_id": "q_20260731_8f3a",
  "created_at": "2026-07-31T18:05:00+08:00",
  "status": "pending"
}
```

**错误码**

| 错误码 | HTTP | 说明 |
|--------|------|------|
| device_id_required | 400 | 缺 device_id |
| question_required | 400 | question 为空 |
| question_too_long | 400 | 超 500 字 |
| senior_store_error | 500 | 存储异常 |

---

### 3.7 GET `/api/senior/answers`（查自己的提问+答复-学生侧）

**Query 参数**：`?device_id=xxx`

**响应 200**

```json
{
  "status": "ok",
  "device_id": "e3f2a1b0-...",
  "questions": [
    {
      "question_id": "q_20260731_8f3a",
      "question": "大一新生需要提前买教材吗？",
      "nickname": "匿名",
      "status": "answered",
      "created_at": "2026-07-31T18:05:00+08:00",
      "reply": {
        "text": "建议等开学后看课程老师通知，先别急着买。",
        "author": "师哥师姐",
        "replied_at": "2026-07-31T19:00:00+08:00"
      }
    },
    {
      "question_id": "q_20260730_1b2c",
      "question": "游泳课怎么选？",
      "nickname": "匿名",
      "status": "pending",
      "created_at": "2026-07-30T10:00:00+08:00",
      "reply": null
    }
  ]
}
```

**说明**：返回该 device 全部提问，按创建时间倒序。`status`：`pending` / `answered`。前端据此轮询刷新（建议每 30s 一次）。

**错误码**

| 错误码 | HTTP | 说明 |
|--------|------|------|
| device_id_required | 400 | 缺 device_id |

---

### 3.8 POST `/api/senior/reply`（师哥师姐入答-管理侧）

**请求体**

```json
{
  "token": "admin-token-xxx",
  "question_id": "q_20260731_8f3a",
  "text": "建议等开学后看课程老师通知，先别急着买。",
  "author": "师哥师姐"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| token | string | 是 | 管理 token（`SENIOR_ADMIN_TOKEN` 环境变量，默认 `dev-senior-token`） |
| question_id | string | 是 | 待答复问题 id |
| text | string | 是 | 答复正文 |
| author | string | 否 | 默认"师哥师姐" |

**响应 200**

```json
{ "status": "ok", "question_id": "q_20260731_8f3a", "status": "answered" }
```

**错误码**

| 错误码 | HTTP | 说明 |
|--------|------|------|
| token_required | 401 | 缺 token 或 token 错误 |
| question_required | 400 | 缺 question_id |
| reply_text_required | 400 | 答复为空 |
| question_not_found | 404 | 问题不存在 |

> **人工维护方式（可选）**：也可以直接在 `senior_replies/` 目录放 `<question_id>.md` 文件，内容为答复文本；后端每 60s 扫描自动导入（见 DEPLOY.md / README）。适合师哥师姐不会用接口、直接编辑文件。

---

### 3.9 GET `/api/senior/pending`（待答复列表-管理侧）

**Query 参数**：`?token=xxx`

**响应 200**

```json
{
  "status": "ok",
  "pending": [
    {"question_id": "q_20260731_8f3a", "question": "大一新生需要提前买教材吗？", "nickname": "匿名", "created_at": "..."}
  ]
}
```

**错误码**：`token_required` 401。

---

### 3.10 GET `/api/corpus/stats`

语料统计（健康检查/调试）。

**响应 200**

```json
{
  "documents": 26,
  "chunks": 122,
  "authority_tiers": {"official_policy": 5, "official_guidance": 8, "peer_experience": 3, "resource": 6, "unverified": 4},
  "parse_status": {"parsed": 15, "metadata_only_missing_pypdf": 0, "ocr_required": 2, "resource_only": 5, "not_downloaded": 4}
}
```

---

### 3.11 POST `/api/session/reset`（可选）

**请求体**：`{"session_id": "abc"}`。**响应**：`{"status": "ok", "session_id": "abc"}`。前端"新对话"按钮调用。

---

## 4. Cookie / 本地存储契约（师哥师姐功能前端实现）

> 目标：**同设备再访问能看到自己的提问与师哥师姐答复**。不依赖后端用户体系，纯浏览器侧标识。

### 4.1 键名与格式

| 键 | 存储 | 格式 | 有效期 | 容量策略 |
|----|------|------|--------|----------|
| `xh_device_id` | Cookie | UUID 字符串（如 `e3f2a1b0-9c8d-4e5f-8a7b-6c5d4e3f2a1b`） | 1 年（`max-age=31536000`） | ~40B，固定 |
| `xh_questions` | Cookie | JSON 数组字符串：`["q_20260731_8f3a","q_20260730_1b2c"]` | 1 年 | 只存**最近 20 条** question_id（每条约 20~30B，总计 < 1KB，远低于 4KB 上限） |
| `xh_chat_history` | localStorage | JSON 数组：`[{role, content, time}]` | 持久 | 聊天正文兜底，最多 200 条（前端自行裁剪），localStorage 无 4KB 限制 |

### 4.2 关键规则（前端必须遵守）

1. **首次访问**：若无 `xh_device_id`，生成 UUID（可用 `crypto.randomUUID()` 或自写 UUIDv4 函数），写入 cookie，`path=/; max-age=31536000; SameSite=Lax`。
2. **提问时**：
   - `POST /api/senior/ask` 请求体带 `device_id`（从 cookie 读）。
   - 成功后把返回的 `question_id` 追加进 `xh_questions` cookie（JSON 数组，**去重**，超 20 条删最旧）。
   - 同时把「我的提问 + 待答复占位」写进 `xh_chat_history`（localStorage）。
3. **进入页面/定时轮询**：
   - 读 `xh_questions` 里所有 question_id，调 `GET /api/senior/answers?device_id=xxx`（服务端已按 device 返回全部，前端也可本地过滤）。
   - 若某条 `status === 'answered'`，把答复渲染进聊天区（"师哥师姐"气泡），并更新 localStorage。
4. **cookie 大小自检**：`xh_questions` 编码后若超 1KB（正常不会），截断到最近 10 条。
5. **跨域注意**：cookie 只在同源下可靠；因此**前端页面和 API 必须同源**（推荐 0.2 的相对路径方案），不要跨域调师哥师姐接口。
6. **SameSite**：`SameSite=Lax`，不需要 `Secure`（本地 http 可用；线上 https 加 `Secure` 无害）。

### 4.3 前端代码骨架（供虾虾参考）

```js
function getDeviceId() {
  let id = getCookie('xh_device_id');
  if (!id) { id = crypto.randomUUID(); setCookie('xh_device_id', id, 31536000); }
  return id;
}
function pushQuestionId(qid) {
  let list = JSON.parse(getCookie('xh_questions') || '[]');
  list = [qid, ...list.filter(x => x !== qid)].slice(0, 20);
  setCookie('xh_questions', JSON.stringify(list), 31536000);
}
```

---

## 5. 未知判定规则（answerability，前端需理解）

后端在**每次问答都走完整检索**后判定 `answerable`：

| 判定 | answerability | can_generate / answerable | 触发条件 |
|------|---------------|---------------------------|----------|
| 直接支持 | supported / supported_with_context / supported_freshness_unverified / mixed_sources_review_required | true | 有证据且覆盖足够 |
| 仅学生经验 | experience_only | true（answer 会标注"学生经验，非官方规定"） | 只有 peer 证据，无官方 |
| 官方证据不足 | insufficient_official_evidence | **false** | 政策/程序类问题但无官方/指导材料 |
| 低分 | insufficient / insufficient_low_score | **false** | top1 score < 阈值（默认 0.06，见环境变量 `ANSWERABILITY_SCORE_FLOOR`） |
| 无证据 | insufficient / insufficient_no_evidence | **false** | top_k 结果为空 |
| 覆盖不足 | insufficient_low_coverage | **false** | 证据概念覆盖 < 0.22（`ANSWERABILITY_COVERAGE_FLOOR`） |
| 证据不足 | insufficient_evidence_count | **false** | 有效证据数 < 2（`ANSWERABILITY_MIN_EVIDENCE`） |
| 追问脱节 | insufficient_contextual_evidence | **false** | 追问与上文主题覆盖不足 |

> 阈值可通过环境变量覆盖（`ANSWERABILITY_SCORE_FLOOR` / `ANSWERABILITY_COVERAGE_FLOOR` / `ANSWERABILITY_MIN_EVIDENCE`），默认值以 `/api/health` 返回的实际判定为准。**v1.2 起默认：0.06 / 0.22 / 2**——强相关问题（如"宿舍是几人间""GPA怎么算"）仍可答出，弱相关/检索不足更倾向拒答。**任何 `unknown=true` 响应前端都只展示拒答文案，不渲染佐证卡片。**

---

## 6. PHP 站集成（不动现有 api.php 文件，仅说明建议改动）

现有 `api.php` 与 `index.html` **不修改**（虾虾负责新前端；本契约仅供运维参考）：

1. **触发即时索引**：在 `api.php` 的上传 / 删除 / 置顶 / 二维码更新等成功分支，追加一行（约在文件保存后）：
   ```php
   @file_get_contents('http://127.0.0.1:8010/api/ingest', false, stream_context_create([
     'http' => ['method' => 'POST', 'header' => 'Content-Type: application/json', 'content' => '{}', 'timeout' => 3]
   ]));
   ```
   失败不影响主流程（`@` 抑制 + 3s 超时）。
2. **反代配置**（宝塔 Nginx，站点的 server 块内）：
   ```nginx
   location /copilot/ {
       proxy_pass http://127.0.0.1:8010/;
       proxy_set_header Host $host;
       proxy_set_header X-Real-IP $remote_addr;
       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
   }
   ```
   > 注意 `proxy_pass http://127.0.0.1:8010/;` 结尾的 `/` 会把 `/copilot/api/chat` 重写为 `/api/chat`，后端无需感知前缀。
3. **supervisor / systemd**：见 `DEPLOY.md`。
4. **CORS**：后端已返回 `Access-Control-Allow-Origin: *`，若前端在 PHP 站同源反代下访问则无需 CORS。

---

## 7. 变更记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-07-31 | 初版：全部接口 + cookie 契约 + 未知判定规则 |
| v1.1 | 2026-08-01 | 新增图片佐证通道：`visual_evidence` 字段（chat/search 响应）；图片块不入 results、不参与 answerability；拒答也返回图片佐证卡片；新增 §3.2.2 字段说明与前端渲染指引 |
| v1.2 | 2026-08-03 | ①彩蛋文件不入库（qa/joke.md、段子/冷笑话等）；②未知判定阈值上调为 0.06/0.22/2，弱相关更倾向拒答；③answer 改为 markdown 排版（结论加粗→分点说明→[Sx]），检索按文档多样性组装、支持多来源引用（S1/S2/S3…），citation_metadata 覆盖全部来源；④新增 `/qa/` 静态映射，FAQ/markdown file_path 指向真实 md 文件（如 `/qa/新生常见问题.md`），引用打开按钮规则：无可用路径不渲染 |
