# Campus Onboarding Copilot

A runnable, trust-aware grounded-chat prototype for incoming students. The
included CUCHIC adapter uses the public corpus at
`https://hic.zihuanana.top/` as a demo instance.

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

The answer layer follows a student-ambassador contract: identify whether the
student needs an observed cohort outcome, a current rule, campus experience, or
general career guidance; answer the actual question first; keep audit citation
tokens out of the visible prose; and show only the sources actually used below
the answer. A historical outcome such as `14 of 85 students` is never silently
promoted into a permanent official quota.

The product surface is a mobile-first fusion of the original student resource
library and the grounded assistant. Files, useful links, tags, and all original
student Q&A remain directly browsable and searchable. The Copilot opens as a
bottom sheet on phones and a floating panel on larger screens, so asking a
question never replaces access to the source library. Multi-turn follow-up,
source provenance, unresolved boundaries, and human handoff are rendered in the
same panel.

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

The runtime has one lightweight required parser, `pypdf`, because governed
web retrieval may read registered official PDF sources in real time.

```bash
make sync       # download manifest, Q&A, and public files
make build      # normalize, chunk, and build SQLite indexes
make audit      # inspect authority, parsing, privacy, and freshness gaps
make evaluate   # run labeled retrieval and answerability checks
make evaluate-chat # check citation, refusal, and peer-label contracts
make publication-audit # reject tracked secrets and generated/private corpus files
make demo       # run an uncertainty-sensitive example query
make serve      # open http://127.0.0.1:8000
```

For a short-lived remote preview, set a strong one-time access code before
starting the server. This enables a login gate, signed four-hour HttpOnly
cookie, same-origin POST checks, per-client login/chat rate limits, no-store
caching, and browser security headers:

```bash
CAMPUS_PREVIEW_ACCESS_CODE='at-least-16-random-characters' \
CAMPUS_PREVIEW_ALLOWED_ORIGINS='https://exact-preview-host.example' make serve
```

Expose that protected local process only through a temporary HTTPS tunnel and
stop both processes after testing. Quick tunnels are a development convenience,
not a production deployment or an authorization service. Never commit the
access code or enter sensitive personal information in a preview environment.
When a reverse proxy changes the origin-facing `Host`, configure only its exact
HTTPS public origin. Wildcard tunnel origins are intentionally unsupported.

DOCX and XLSX body extraction, plus the development toolchain, are enabled
when optional packages are installed:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[parsers,dev]'
```

Without the optional packages, the full FAQ, links, and PDFs remain supported;
DOCX/XLSX files are kept in the catalog with `metadata_only` parse status. The
system will not pretend an unparsed document supports an answer.

`make` automatically uses `.venv/bin/python` when that environment exists, so
a complete index is not accidentally rebuilt with a parser-free system Python.

## API

The lightweight standard-library HTTP server exposes:

- `GET /api/health`
- `GET /api/corpus/stats`
- `GET /api/library`
- `GET /files/<manifest-listed filename>`
- `POST /api/search`
- `POST /api/context`
- `POST /api/chat`
- `POST /api/session/reset`

`/api/library` is a read-only projection of the synchronized original source
manifest and student Q&A. It omits administrative fields. `/files/` serves only
visible, manifest-listed local attachments and rejects path traversal; run
`make sync` first when the raw source files are not present locally.

Example:

```bash
curl -s http://127.0.0.1:8000/api/search \
  -H 'content-type: application/json' \
  -d '{"query":"宿舍是几人间","profile":{"cohort":"2026"}}'
```

`/api/chat` adds a bounded in-memory conversation (the most recent four turns),
uses prior user intent to resolve explicit follow-ups, retrieves fresh evidence
for every turn, and returns both an audit answer and a citation-free
`display_answer`, plus only the cited `sources` for student-facing rendering.
It also returns a `trace_id` for correlating the answer with its local execution
trace.

## RAG execution traces

The development runtime records a privacy-aware trace for each chat request in
the ignored local database `data/runtime/rag_traces.db`. A trace shows the
redacted query, contextualized retrieval query, local and web candidates,
selected and rejected evidence, answerability decision, model/provider and
fallback path, grounding validation, final response, citations, errors, and
per-stage latency. It intentionally does not store hidden model reasoning,
credentials, cookies, or authorization headers. Common email addresses, phone
numbers, and long identifiers are redacted, and session IDs are one-way hashed.

Inspect recent requests or one complete flow with:

```bash
campus-copilot traces list --limit 20
campus-copilot traces show trace_<id>
```

Tracing is enabled by default for development and retained for 30 days. Change
the local behavior without editing code:

```dotenv
CAMPUS_TRACE_ENABLED=0
CAMPUS_TRACE_RETENTION_DAYS=30
CAMPUS_TRACE_DB_PATH=/absolute/local/path/rag_traces.db
```

For a public deployment, keep this database outside the web root, restrict
operator access, and set a retention period appropriate to the privacy policy.

When the final answer cannot provide both cited evidence and a supported useful
conclusion—including partial web results that do not resolve the core
question—the UI uses the standard human-handoff fallback and offers an explicit,
optional follow-up form. A submitted
email is stored in a separate `human_handoffs` table together with the redacted
query and its `trace_id`; it is not sent to the model and is not added to the
knowledge base. Operators can review the private queue on the server:

```bash
PYTHONPATH=src .venv/bin/python -m campus_copilot.cli handoffs list --limit 20
PYTHONPATH=src .venv/bin/python -m campus_copilot.cli handoffs show handoff_<id>
```

The endpoint requires same-origin browser submission, validates the email and
trace, and limits each client to five submissions per hour. Human-follow-up
records use the same retention period as traces. Because the address must remain
usable for a reply, it is sensitive plaintext operational data: never place the
runtime database under `web/`, commit it, or expose it through a public admin
endpoint.

When local evidence is insufficient, the answer plan can execute governed live
retrieval. `config/web_sources.json` registers reviewed school and public
sources together with query hints, authority, dates, and applicability. The
runtime fetches the current HTML or PDF, enforces an exact HTTPS host allowlist
to prevent SSRF, extracts a focused passage, retries transient failures, and
caches results for 30 minutes. Retrieved school pages are labeled
`official_web`; contextual sources such as a government climate standard remain
separate as `public_web` and cannot substantiate school-policy claims.

Retrieved candidates are not automatically answer evidence. For question types
with explicit objects, the answer layer checks required aspects before adopting
a passage. A certificate-wording question must be covered by
`毕业证/学位证/证书`; a passage that only shares `中外合作办学` is discarded even
when its lexical score is high. Credential questions always run official and
public web enrichment, then rank local and web candidates by question coverage,
direct-answer presence, authority, and retrieval score. An official page that
only states which certificate is awarded cannot prove what is printed on it.

Legacy HTTP-only pages are never added to the live-fetch allowlist. A manually
verified excerpt may be stored as a dated `verified_web_snapshot`, labeled as a
public reference with its uncertainty, while live fetching remains HTTPS-only.

The registry remains the low-latency reviewed source layer. Optional autonomous
discovery is provided through a separate `SearchDiscoveryProvider`; the first
development adapter uses Tavily. When configured, official and public answer routes search
beyond the registry, convert results into the same evidence contract, deduplicate
URLs, and apply the existing authority, coverage, uncertainty, and citation
controls. Official searches accept only `cuc.edu.cn` and its subdomains;
non-official results remain `public_web`. Common email, phone, and long numeric
identifiers are removed before sending a query to the provider.

Makers Models supplies the answer model; it is not itself a web-search service.
Without a search key, the application reports `registry_only` and keeps the
reviewed-source behavior rather than pretending to have searched the open web.
Official WeChat discovery still needs a separate provider or ingestion adapter.

The Tavily adapter is for development evaluation, not the default for a service
deployed in mainland China. A mainland production deployment should use a
domestic provider and endpoint after privacy, source-URL, content-safety, and
network-reliability review. Provider credentials remain separate from Makers.

To evaluate Tavily locally, add these values to ignored `.env.local`:

```dotenv
CAMPUS_WEB_SEARCH_PROVIDER=tavily
CAMPUS_WEB_SEARCH_API_KEY=your-key
```

Then run `make search-check`. The returned `answer_plan` should show
`web_discovery_executed: true`, `web_discovery_provider: tavily`, and the source
cards should label newly found pages as autonomous official/public search.

## Governed official-site corpus

The durable official corpus is maintained separately from per-question web
search. `config/official_crawl.json` defines HTTPS hosts, path prefixes, seeds,
page limits, issuers, and tags. The synchronizer respects `robots.txt`, refuses
redirects outside the allowlist, ignores media links, bounds response size, and
stores content-addressed immutable snapshots under ignored
`data/official_sites/`.

```bash
make official-sync
make official-review
PYTHONPATH=src .venv/bin/python -m campus_copilot.cli official-review \
  --approve 'https://hainan.cuc.edu.cn/example/page.htm'
make build
```

New and changed pages are always `pending`. An unchanged page retains its
review status; a changed checksum creates a new immutable version and removes
the old approval from the active page. Only the latest `approved` snapshot is
converted into an `official_web` document during `make build`. This keeps
automatic discovery separate from authority to publish an answer.

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

The same health response reports whether governed web retrieval is enabled.
The web registry and fetcher contain no model credentials.

## Current corpus boundary

The public site is a useful seed corpus, not a complete official source of
truth. The MVP excludes QR communities and flags record-style spreadsheets as
potentially sensitive. It also distinguishes `uploadTime` from publication and
effective dates.

`evaluate-chat` checks grounding plus a basic direct-answer shape; it is not a
claim that answer quality is solved. Human-labeled completeness, usefulness,
temporal conflicts, tone, and held-out questions remain required before
reporting a production accuracy metric.

## Contributing and release status

The repository is being prepared for shared maintenance. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the branch/PR workflow and
[`docs/COLLABORATION.md`](docs/COLLABORATION.md) for ownership boundaries.
Until the collaborators confirm code and content licenses, treat the repository
as private and do not redistribute the synchronized source corpus.
