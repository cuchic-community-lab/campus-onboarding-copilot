# Campus Onboarding Copilot

A runnable, trust-aware knowledge-base prototype for incoming students. The
included CUCHIC adapter uses the public corpus at
`https://hic.zihuanana.top/` as a demo instance.

The prototype deliberately separates retrieval from generation. It first
proves that the system can find the right evidence, preserve provenance,
recognize unofficial experience, and abstain when evidence is incomplete.
An LLM can then consume the resulting context packet without becoming the
source of truth.

## Why this is an indexed knowledge base

The durable layer is SQLite, not a vector database:

- `documents` stores provenance, authority, dates, applicability, checksums,
  and parse status.
- `chunks` stores meaning-preserving retrieval units and citation locations.
- `chunk_fts` provides inspectable lexical retrieval over Chinese bigrams,
  titles, headings, and tags.
- local hashed subword vectors provide an offline second retrieval channel.
- reciprocal-rank fusion combines the two channels; authority and student
  applicability are explicit ranking features.

The local vector channel is a reproducible baseline, not a claim of deep
semantic understanding. Replace `LocalSubwordVectorizer` with a production
embedding provider later while keeping the document and citation model.

## Domain-aware chunking

- FAQ: one question and its complete answer is one chunk.
- Policy/handbook: preserve heading path and page, then group complete
  paragraphs into roughly 350-800 Chinese characters.
- Procedures: keep numbered steps together whenever possible.
- Forms and external links: model them as resources/actions instead of using
  them as factual answer passages.
- Images and scanned PDFs: remain non-assertable until OCR succeeds.

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

## API

The zero-dependency server exposes:

- `GET /api/health`
- `GET /api/corpus/stats`
- `POST /api/search`
- `POST /api/context`

Example:

```bash
curl -s http://127.0.0.1:8000/api/search \
  -H 'content-type: application/json' \
  -d '{"query":"宿舍是几人间","profile":{"cohort":"2026"}}'
```

`/api/context` returns a model-ready evidence packet and response policy. It
does not call a model. A future provider receives only this packet and must:

1. cite the supplied evidence IDs;
2. label peer experience as peer experience;
3. never upgrade `unknown`, `likely`, or `inferred` into a fact;
4. abstain when the packet says `can_generate=false`;
5. preserve year, cohort, major, and campus applicability.

## Current corpus boundary

The public site is a useful seed corpus, not a complete official source of
truth. The MVP excludes QR communities and flags record-style spreadsheets as
potentially sensitive. It also distinguishes `uploadTime` from publication and
effective dates.
