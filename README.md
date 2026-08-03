# Campus Onboarding Copilot (XiaohaiGPT)

A runnable, trust-aware grounded-chat prototype for incoming students. It
indexes the campus site's real corpus (`files.json` / `files/` / `qa/` /
`qrcodes.json` / `qrcodes/`) and answers only from retrieved evidence —
refusing ("未知/未找到相关材料") whenever evidence is missing or below
threshold.

> 2026-07-31 增量升级（v0.3.0）：站点直读 + 即时增量索引 + 师哥师姐问答 +
> citations 完整元数据 + 一键启动 + 线上宝塔部署。详见下方各节与 `DEPLOY.md`。

## Origin and collaboration

The original HIC onboarding knowledge base and public source site were created
and are maintained by **Zihuanana**. **Pengwei Fu** designed and implemented
the retrieval, grounded-generation, evaluation, and cloud-model integration
layers in this repository. New work is intended to be developed through issues,
reviewed pull requests, and an explicit shared-maintenance agreement.

This is an independent student-built project. It is not an official university
service, and retrieved policies must still be checked against the latest school
notice. Source attribution does not by itself grant a license to redistribute
the underlying documents; code and content licensing will be documented
separately before a public release.

The system separates retrieval from generation. It finds evidence, preserves
provenance, recognizes unofficial experience, and abstains when evidence is
incomplete. A configurable model may compose the final answer, but it never
becomes the source of truth. Without a model credential, an auditable local
composer keeps the complete product path runnable.

## Why this is an indexed knowledge base

The durable layer is SQLite, not a vector database:

- `documents` stores provenance, authority, dates, applicability, checksums,
  and parse status.
- `chunks` stores meaning-preserving retrieval units and citation locations.
- `chunk_fts` provides inspectable lexical retrieval over Chinese bigrams,
  titles, headings, and tags.
- local hashed subword vectors provide an offline second retrieval channel.
- reciprocal-rank fusion combines the two channels; semantic/attribute
  relevance and student applicability dominate ranking. Authority is retained
  as answer metadata and only a small near-tie signal, so a directly relevant
  student measurement is not suppressed by an unrelated official passage.

The local vector channel is a reproducible baseline, not a claim of deep
semantic understanding. Replace `LocalSubwordVectorizer` with a production
embedding provider later while keeping the document and citation model.

## Domain-aware chunking

- FAQ: one question and its complete answer is one chunk.
- Safe student spreadsheets: one measured item per structured fact chunk.
- Policy/handbook: preserve heading path and page, then group complete
  paragraphs into roughly 350-800 Chinese characters.
- Procedures: keep numbered steps together whenever possible.
- Forms and external links: model them as resources/actions instead of using
  them as factual answer passages.
- Images and scanned PDFs: remain non-assertable until OCR succeeds.

Student-authored guides and measurements are first-class evidence in this
student-built product. They are cited as student experience rather than
silently upgraded to school policy. Privacy-sensitive record spreadsheets
remain quarantined.

No fixed-token splitter is used across all sources. Fixed token windows cut a
condition away from its rule, split a procedure in the middle, and destroy FAQ
question-answer boundaries.

## Run

The core prototype has no required third-party Python packages.

```bash
# 一键启动（Windows / Linux / macOS）：检查依赖 → 增量建索引 → 8000 端口
start.bat      # Windows 双击即可
bash start.sh  # Linux / macOS

# 或者手动：
make build      # normalize, chunk, and build SQLite indexes
make serve      # open http://127.0.0.1:8000
```

### 数据源（站点直读，本地=线上同构）

后端自动探测站点根目录（向上查找含 `files.json` 的目录），直接读取：

| 数据源 | 内容 | 索引方式 |
|--------|------|----------|
| `files.json` | 文档清单（files[] + links[]） | checksum diff 增量 |
| `files/` | 官方 PDF/Excel/图片 | pypdf/docx/openpyxl 解析（缺则 metadata_only 降级） |
| `qa/` | 问答库 markdown | 每题一问一答 chunk |
| `qrcodes.json` | 二维码清单 | 每条切块入库，图片作佐证 |
| `qrcodes/` | 二维码图片 | 静态映射 `/qrcodes/*` |

**即时性**：`POST /api/ingest` 触发增量重建（只重切变更文档）；启动时 + 每 60s
轮询 `files.json`/`qrcodes.json` 变化自动重切。

### 未知判定（材料没有就说未知）

每次问答都完整走检索 → 判定 → 再决定回答或拒答：

- 检索为空 / top1 分数 < `ANSWERABILITY_SCORE_FLOOR`（默认 0.02）
- 概念覆盖 < `ANSWERABILITY_COVERAGE_FLOOR`（默认 0.15）
- 数值型问题但证据无数值
- 查询核心实体不在证据中（防止"游泳池水深"被"游泳"泛内容糊弄）

任一命中 → `answerable=false, unknown=true` → 明确拒答，不编造。

### 图片佐证通道（v0.3.1）

`files/` 下的图片（GPA折算表、摆渡车线路图等，`media_type=image`）不 OCR、不断言
事实，但会生成 `visual_reference` 佐证引用块：检索命中后通过 `visual_evidence`
字段独立返回，前端渲染"相关图片佐证"卡片（`image_thumb` 直接展示原图）。

- 图片块**不进入** `results`，**不参与** answerability 判定
- 即使 `unknown=true`（严格拒答），`visual_evidence` 仍会带出图片，引导用户看图
- 示例：问"成绩折算"→ 拒答文案 + GPA折算表.jpg 佐证卡片；问"摆渡车线路"→
  FAQ 回答 + 摆渡车线路图.png 佐证卡片

### 师哥师姐问答

- 学生侧：`POST /api/senior/ask` 提问入队；`GET /api/senior/answers?device_id=xxx` 查自己提问+答复
- 管理侧：`POST /api/senior/reply`（带 `SENIOR_ADMIN_TOKEN`）入答；或直接把
  `senior_replies/<question_id>.md` 文件放到站点根下，后端 60s 轮询自动导入
- 前端 cookie 契约：`xh_device_id`（UUID）+ `xh_questions`（最近 20 条 question_id），
  正文存 localStorage —— 详见 API 契约文档

### AI 辅助切分（可选）

`AI_ASSISTED_CHUNKING=0`（默认，规则切分零依赖）或 `=1`（用 LLM 增强标签）。
API Key 在 `.env.local` 配置：`CAMPUS_LLM_API_KEY` / `CAMPUS_LLM_BASE_URL` /
`CAMPUS_LLM_MODEL`（EdgeOne Makers 预设）。

PDF, DOCX, and XLSX body extraction is enabled when optional parser packages
are installed:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[parsers,dev]'
```

Without them, the full FAQ and links are searchable and binary documents are
kept in the catalog with `metadata_only` parse status. The system will not
pretend an unparsed PDF supports an answer.

`make` automatically uses `.venv/bin/python` when that environment exists, so
a complete index is not accidentally rebuilt with a parser-free system Python.

## API

The zero-dependency server exposes:

- `GET /api/health`
- `GET /api/corpus/stats`
- `POST /api/search`
- `POST /api/context`
- `POST /api/chat`
- `POST /api/ingest`（即时增量索引）
- `POST /api/session/reset`
- `POST /api/senior/ask` / `GET /api/senior/answers` / `POST /api/senior/reply` / `GET /api/senior/pending`

> 完整字段定义、cookie 契约、错误码、前端对接注意事项见
> `docs/API_CONTRACT.md`（或联调输出目录 contract.md）。

Example:

```bash
curl -s http://127.0.0.1:8000/api/search \
  -H 'content-type: application/json' \
  -d '{"query":"宿舍是几人间","profile":{"cohort":"2026"}}'
```

`/api/chat` adds a bounded in-memory conversation (the most recent four turns),
uses prior user intent to resolve explicit follow-ups, retrieves fresh evidence
for every turn, and returns an answer with inspectable source objects.

`/api/context` returns the same model-ready evidence packet and response policy.
A provider receives only this packet and must:

1. cite the supplied evidence IDs;
2. label peer experience as peer experience;
3. never upgrade `unknown`, `likely`, or `inferred` into a fact;
4. abstain when the packet says `can_generate=false`;
5. preserve year, cohort, major, and campus applicability.

## Optional economical model

Any chat-completions endpoint that follows the OpenAI-compatible request shape
can be used without adding a Python SDK. Configuration is entirely external:

```bash
export CAMPUS_LLM_BASE_URL="https://your-provider.example/v1"
export CAMPUS_LLM_MODEL="your-economical-chat-model"
export CAMPUS_LLM_API_KEY="..."
make serve
```

The adapter requests structured claims and citations. Its output is checked
against the current evidence IDs and source authority. Invalid output or a
provider outage automatically falls back to the local composer. The current
session store is intentionally process-local; persistence, account isolation,
and cross-device history belong to a later production phase.

### Tencent EdgeOne Makers Models

The repository includes a provider preset for Makers Models. Create a dedicated
API key in `Makers > Models > API Key`, then keep it in the ignored local file:

```bash
cp .env.example .env.local
# Edit .env.local and set CAMPUS_LLM_API_KEY without committing the file.
make makers-check
make serve
```

The preset selects `https://ai-gateway.edgeone.link/v1` and
`@makers/deepseek-v4-flash`. Either can still be overridden through
`CAMPUS_LLM_BASE_URL` and `CAMPUS_LLM_MODEL`, preserving provider portability.
`GET /api/health` reports the provider, model, and whether a credential is
configured, but never returns the credential itself.

## Current corpus boundary

The public site is a useful seed corpus, not a complete official source of
truth. The MVP excludes QR communities and flags record-style spreadsheets as
potentially sensitive. It also distinguishes `uploadTime` from publication and
effective dates.

`evaluate-chat` is a contract check, not a claim that answer quality is solved.
Human-labeled completeness, usefulness, temporal conflicts, and held-out
questions remain required before reporting a production accuracy metric.

## Contributing and release status

The repository is being prepared for shared maintenance. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the branch/PR workflow and
[`docs/COLLABORATION.md`](docs/COLLABORATION.md) for ownership boundaries.
Until the collaborators confirm code and content licenses, treat the repository
as private and do not redistribute the synchronized source corpus.
