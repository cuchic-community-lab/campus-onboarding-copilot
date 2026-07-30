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
- `POST /api/search`
- `POST /api/context`
- `POST /api/chat`
- `POST /api/session/reset`

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

This is real retrieval, but deliberately not arbitrary search. New websites and
official-account sources must first be added to the reviewed registry. Automatic
discovery across the open web or WeChat requires a separate search provider
(for example Tencent Web Search API or TokenHub), followed by the same domain,
authority, freshness, and citation controls. Makers Models supplies the answer
model; it is not itself a web-search service.

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
