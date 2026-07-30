# Campus Onboarding Copilot

A runnable, trust-aware grounded-chat prototype for incoming students. The
included CUCHIC adapter uses the public corpus at
`https://hic.zihuanana.top/` as a demo instance.

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
make sync       # download manifest, Q&A, and public files
make build      # normalize, chunk, and build SQLite indexes
make audit      # inspect authority, parsing, privacy, and freshness gaps
make evaluate   # run labeled retrieval and answerability checks
make evaluate-chat # check citation, refusal, and peer-label contracts
make demo       # run an uncertainty-sensitive example query
make serve      # open http://127.0.0.1:8000
```

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
- `POST /api/session/reset`

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

## Current corpus boundary

The public site is a useful seed corpus, not a complete official source of
truth. The MVP excludes QR communities and flags record-style spreadsheets as
potentially sensitive. It also distinguishes `uploadTime` from publication and
effective dates.

`evaluate-chat` is a contract check, not a claim that answer quality is solved.
Human-labeled completeness, usefulness, temporal conflicts, and held-out
questions remain required before reporting a production accuracy metric.
