# Campus Onboarding Copilot

Campus Onboarding Copilot is an evidence-grounded AI customer service agent for
incoming university students. It retrieves policies, service manuals, student
guides, FAQs, and reviewed web sources; produces cited answers; and routes
unresolved cases to human follow-up.

## Try the live product

**[Launch XiaohaiGPT →](https://hic.zihuanana.top/xiaohaigpt.html)**

[Browse the HIC onboarding source library](https://hic.zihuanana.top/)

### Grounded answer and human follow-up

![XiaohaiGPT answering a dormitory question with cited evidence and a human follow-up action](docs/assets/student-grounded-answer.png)

Start with this question:

```text
宿舍的床多大？
```

The response gives a direct answer, identifies the information as prior student
experience, exposes the supporting-evidence drawer, and keeps the human
follow-up action in the same service flow.

Two additional scenarios demonstrate different agent behaviors:

| Scenario | Example query | What to inspect |
| --- | --- | --- |
| Official procedure | `新生第一次选课怎么操作？` | Ordered SOP evidence, cohort applicability, and citations |
| Coverage gap | Ask for a current rule absent from the corpus | Unresolved boundary and human follow-up route |

### Searchable source library

![HIC source library with document filters, official policies, manuals, links, and student resources](docs/assets/source-library-files.png)

![Student-maintained onboarding Q&A grouped by topic](docs/assets/source-library-qa.png)

Students can inspect the underlying documents and curated Q&A directly. The
same library also gives operators a visible view of the corpus used by the
assistant.

## Problem and product goal

New students ask recurring questions about dormitories, course registration,
campus services, and university policies. The information is distributed
across policy documents, service manuals, student guides, spreadsheets, and
webpages, while users usually begin with a natural-language question rather
than a document name or department.

The product goal is **end-to-end issue resolution**. A complete service journey
includes:

1. understanding the user’s intent and recent conversation context;
2. identifying the required answer type and student applicability;
3. selecting local or governed web retrieval tools;
4. deciding whether the available evidence is sufficient;
5. composing and validating a cited response;
6. handing the case to a human when coverage is insufficient;
7. recording the execution path for evaluation and iteration.

## System architecture

![Campus Onboarding Copilot architecture from student request through retrieval, validation, handoff, and evaluation](docs/assets/system-architecture.png)

The agent core identifies the question type and the evidence required. Formal
policy questions require official sources; campus-experience questions can use
peer evidence with an explicit experience label.

The agent then calls the local knowledge base or governed web retrieval tools.
Candidates pass through applicability filtering, rank fusion, coverage checks,
and an answerability decision before they enter the model evidence packet.

The model composes the response from that packet. Citation rules, source
authority, answerability, and refusal behavior remain in the application layer.
The validator checks the generated claims and citations before delivery. Cases
without sufficient support enter the human follow-up queue.

### Request lifecycle

```text
question + student profile + recent turns
  → query contextualization
  → question type and answer-shape planning
  → applicability filters
  → FTS5 lexical retrieval + local subword retrieval
  → reciprocal-rank fusion and coverage reranking
  → optional allowlisted official/public web retrieval
  → answerability decision
  → evidence packet for the LLM
  → claim, citation, and authority validation
  → grounded response or human handoff
  → execution trace and evaluation
```

## Key product decisions

### 1. Hybrid retrieval

FTS5 retrieves exact terms such as policy names, form names, dates, course
types, titles, and headings. A local hashed subword channel handles variations
in Chinese phrasing. Reciprocal-rank fusion combines the lexical and subword
candidate lists before coverage and applicability reranking.

SQLite is the inspectable system of record for the current corpus. The logical
document and chunk model can later move to PostgreSQL and pgvector as traffic,
concurrent writes, or embedding requirements grow.

### 2. Source authority and answerability

Retrieval relevance and permission to assert a claim are evaluated separately.
A peer-authored dormitory measurement may be highly relevant to a lifestyle
question and still needs an explicit student-experience label. Formal policy
claims require official evidence.

Every evidence object retains its source, authority type, dates, student level,
cohort, major, campus, uncertainty markers, and assertion policy. Before
generation, the planner assigns an answerability state such as:

- `supported`
- `experience_only`
- `supported_with_context`
- `supported_with_unresolved_wording`
- `insufficient_official_evidence`

Generation proceeds when the selected evidence covers the required answer
aspects. The final response preserves source type, uncertainty, and
applicability.

### 3. Human handoff as a service outcome

Evidence gaps, conflicting sources, and individual exceptions may require
human judgment. The client offers an optional email follow-up when the agent
cannot support a useful conclusion.

The private queue stores the redacted query, trace ID, and submitted address.
The address stays outside the model context and knowledge index. Linking the
case to its trace gives the operator the retrieval and validation context needed
to continue the conversation.

## Response controls

The post-generation validator currently checks that:

- every citation resolves to evidence supplied for the current request;
- factual claims remain linked to cited evidence IDs;
- policy claims use an assertable official source;
- peer evidence retains its student-experience label;
- uncertainty and student applicability remain visible;
- an insufficient-evidence decision returns a refusal and follow-up route.

When the provider is unavailable or its output fails validation, an extractive
local composer keeps the request path operational. Claim-level
natural-language entailment and temporal-conflict detection remain planned
work.

## Evaluation and iteration

Evaluation spans retrieval, response quality, customer-service outcomes,
reliability, and safety.

| Layer | Metric or check | What it measures |
| --- | --- | --- |
| Retrieval | Recall@K, MRR, source-type accuracy, applicability | Whether the agent found the right evidence and ranked it early |
| Response | Citation validity, claim-to-evidence support, unsupported-claim rate | Whether each factual statement is grounded in the supplied evidence |
| Service | Self-service resolution, handoff, repeat contact, helpfulness/CSAT | Whether the student reached a useful endpoint |
| Operations | Stage latency, fallback rate, error category, cost per resolved case | Whether the service runs reliably and economically |
| Safety | PII leakage, stale-policy use, authority escalation | Whether the agent remains inside its operating boundary |

The repository currently includes labeled retrieval cases and chat-contract
checks for citation, refusal, authority, and response shape. Stable service
metrics require a larger set of real, human-labeled interactions before they
can be reported.

### Query traces

Each request produces a privacy-aware trace containing:

- the redacted query and contextualized retrieval query;
- local and web candidates, including rejected evidence;
- selected evidence and the answerability decision;
- provider, model, and fallback path;
- validation result, final answer, citations, errors, and stage latency;
- environment and request source for separating evaluation from browser traffic.

These traces support a failure taxonomy covering knowledge gaps, retrieval
misses, stale or conflicting sources, incorrect refusals, generation errors,
provider failures, and cases that genuinely require human support. Frequency
and user impact then guide the next product iteration.

Traces exclude model credentials, cookies, authorization headers, and hidden
model reasoning. Common emails, phone numbers, and long identifiers are
redacted; session identifiers are one-way hashed. Production traces and opt-in
handoffs use a 30-day default retention period.

```bash
campus-copilot traces list --environment production --source browser --limit 50
campus-copilot traces show trace_<id>
campus-copilot handoffs list --limit 50
```

## Collaboration and ownership

This project is maintained through independent branches and reviewed pull
requests.

| Branch | Role | Status |
| --- | --- | --- |
| `main` | RAG, answerability, evaluation, tracing, handoff, and single-server deployment baseline | Reviewed integration branch |
| `xiaohaigpt` | Live student experience, admin/knowledge workflows, and senior Q&A | Experimental integration branch |

The original HIC onboarding knowledge base and public source site are created
and maintained by **Zihuanana**. **Pengwei Fu** designed and implemented the
retrieval, grounded generation, evaluation, cloud-model integration, tracing,
handoff, and production deployment layers in this repository.

Capabilities move from `xiaohaigpt` to `main` through scoped pull requests with
tests and review. See [`CONTRIBUTING.md`](CONTRIBUTING.md) and
[`docs/COLLABORATION.md`](docs/COLLABORATION.md) for the shared workflow.

## Roadmap

- build a larger human-labeled benchmark from real query failures;
- add claim-level entailment and temporal-conflict evaluation;
- expand reviewed official-site and official-account coverage;
- add owner, status, SLA, and resolution outcome to the handoff workflow;
- compare retrieval, prompt, and conversation-strategy changes through offline
  gates and controlled experiments;
- introduce structured action tools after the factual support path meets its
  quality threshold.

## Technical reference

The sections below cover installation, configuration, APIs, quality gates, and
deployment for contributors and reviewers who want to inspect the implementation.

### Core capabilities

| Capability | Implementation |
| --- | --- |
| Multi-turn support | Bounded conversation context with fresh retrieval on every turn |
| Hybrid retrieval | SQLite FTS5, local hashed subword vectors, reciprocal-rank fusion, and coverage reranking |
| Knowledge governance | Provenance, authority, dates, applicability, checksums, parse status, and review state |
| Grounded generation | Versioned evidence packet, structured claims, citations, and post-generation validation |
| Retrieval tools | Local knowledge, governed official web retrieval, and optional public web discovery |
| Human handoff | Opt-in follow-up queue linked to the redacted query and execution trace |
| Observability | Candidate evidence, selection decisions, fallback path, validation result, errors, and latency |
| Deployment | Docker Compose, Caddy HTTPS, health checks, persistent runtime volume, and rate limiting |

### Knowledge ingestion

| Source | Retrieval unit | Policy |
| --- | --- | --- |
| FAQ | Complete question and answer | Preserves scope and caveats |
| Policy or handbook | Heading-aware paragraphs | Retains conditions, exceptions, page, and effective date |
| Service manual or SOP | Ordered steps | Preserves procedure sequence |
| Safe student measurement | One structured item | Makes each fact independently citable |
| Form or link | Resource/action record | Offered as a next step, excluded from policy claims |
| Image or scanned PDF | Metadata until successful parsing | Excluded from factual answers while content is unavailable |
| Personal-record spreadsheet | Quarantined | Excluded from indexing and public retrieval |

Official-site synchronization and per-request web retrieval use separate
workflows. New or changed official pages enter a review queue, and a checksum
change resets the previous approval before the content returns to the local
index.

### Requirements

- Python 3.9 or later
- `make`
- network access for corpus synchronization
- optional model and search credentials for provider-backed generation and web
  discovery

### Install and run

```bash
git clone git@github.com:cuchic-community-lab/campus-onboarding-copilot.git
cd campus-onboarding-copilot

python3 -m venv .venv
.venv/bin/pip install -e '.[parsers,dev]'

make sync
make build
make audit
make serve
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

`make sync` downloads the public demo manifest and files. `make build`
normalizes, chunks, and indexes the corpus. The installation command above also
enables DOCX and XLSX parsing.

### Configuration

Copy the example file and keep the populated file untracked:

```bash
cp .env.example .env.local
```

| Variable | Required | Description |
| --- | --- | --- |
| `CAMPUS_LLM_PROVIDER` | For model generation | Provider preset or provider label |
| `CAMPUS_LLM_API_KEY` | For model generation | Server-side model credential |
| `CAMPUS_LLM_BASE_URL` | For custom provider | OpenAI-compatible `/v1` endpoint |
| `CAMPUS_LLM_MODEL` | For custom provider | Model identifier |
| `CAMPUS_LLM_TIMEOUT` | No | Provider timeout in seconds; default `30` |
| `CAMPUS_WEB_SEARCH_PROVIDER` | For autonomous discovery | Search-provider adapter; development supports `tavily` |
| `CAMPUS_WEB_SEARCH_API_KEY` | For autonomous discovery | Separate search credential |
| `CAMPUS_WEB_SEARCH_TIMEOUT` | No | Search timeout in seconds; default `12` |
| `CAMPUS_TRACE_ENABLED` | No | Enables local execution tracing; default `1` |
| `CAMPUS_TRACE_RETENTION_DAYS` | No | Trace and handoff retention; default `30` |
| `CAMPUS_ENVIRONMENT` | No | Trace environment label; default `development` |

The included Makers preset supplies its gateway URL and model name. Any
OpenAI-compatible endpoint can be configured through the base URL, model, and
API key variables. With no model credential, the application uses its local
composer.

### Useful commands

```bash
make query Q="宿舍是几人间，能确定吗？"
make chat Q="新生第一次选课怎么操作？"
make evaluate
make evaluate-chat
make publication-audit
make test
```

### API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Runtime, database, model, and search readiness |
| `GET` | `/api/corpus/stats` | Indexed corpus summary |
| `GET` | `/api/library` | Public projection of synchronized resources |
| `GET` | `/files/<filename>` | Allowlisted manifest attachment |
| `POST` | `/api/search` | Retrieval candidates and scoring metadata |
| `POST` | `/api/context` | Model-ready evidence packet and answer plan |
| `POST` | `/api/chat` | Grounded response, sources, and trace ID |
| `POST` | `/api/session/reset` | Clear the current bounded conversation |

Example retrieval request:

```bash
curl -s http://127.0.0.1:8000/api/search \
  -H 'content-type: application/json' \
  -d '{"query":"宿舍是几人间","profile":{"cohort":"2026"}}'
```

### Testing and quality gates

```bash
make test
make evaluate
make evaluate-chat
make audit
make publication-audit
```

- unit tests cover retrieval, planning, composition, source policy, web
  retrieval, tracing, handoff, preview security, frontend contracts, and
  deployment configuration;
- retrieval evaluation uses labeled questions in
  `evaluation/golden_questions.jsonl`;
- chat evaluation checks citation, refusal, authority, and answer-shape
  contracts;
- publication audit rejects tracked secrets and private/generated corpus files.

### Production deployment

The reviewed beta deployment uses one Tencent Cloud Lighthouse or CVM instance,
Docker Compose, and Caddy. The application container runs without root
privileges; Caddy terminates HTTPS; a named volume persists traces and handoff
records across releases.

Production credentials belong in the ignored `deploy/.env.production` file.
Port `8000` remains private, while Caddy exposes ports `80` and `443`.

See [`deploy/README.md`](deploy/README.md) for provisioning, startup, backup,
upgrade, and release-gate instructions.

### Repository layout

```text
src/campus_copilot/       agent planning, retrieval, composition, tracing, API
config/                   governed web and official-crawl registries
evaluation/               labeled retrieval scenarios
web/                      student-facing responsive client
tests/                    unit and contract tests
docs/                     knowledge model, LLM contract, collaboration notes
deploy/                   Docker/Caddy production configuration and runbook
data/                     reviewed seed corpus and ignored runtime artifacts
```

## Data and licensing boundary

The current corpus covers a limited set of onboarding scenarios. QR-based
communities and privacy-sensitive record spreadsheets remain outside the index.
Code and synchronized content licensing are still under review, so repository
access and corpus redistribution remain restricted until those terms are
documented.
