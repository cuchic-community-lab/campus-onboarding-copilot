# Knowledge model and indexing decision

## Decision

Use an indexed document knowledge base with an explicit metadata store. Do not
use a pure vector store as the system of record and do not model the corpus as
a knowledge graph first.

SQLite is the durable MVP store because the corpus is small, updates are
batch-oriented, the index must be inspectable in an interview, and exact
policy terms matter. A production deployment can move the same tables to
PostgreSQL and put embeddings in pgvector without changing the logical model.

## Entities

```text
Source site
  └── Document
        ├── provenance + checksum
        ├── authority + assertion policy
        ├── publication/effective/upload dates
        ├── cohort/academic year/student level/major/campus
        └── Chunk
              ├── page + heading path + sequence
              ├── chunk type
              ├── uncertainty markers
              └── retrieval indexes
```

`Document` is the provenance and lifecycle boundary. `Chunk` is only a
retrieval unit; it never loses its parent source, page, authority, or
applicability.

Reviewed live sources are a separate, ephemeral evidence layer. Their registry
stores URL, authority, topic hints, and applicability, while each answer records
the actual retrieval time and extracted passage. A fetched webpage does not
silently become a durable knowledge-base document; promotion into the indexed
corpus requires the normal ingestion and review process.

## Chunking policy

| Source | Unit | Why |
| --- | --- | --- |
| FAQ | complete question + complete answer | keeps the question's scope and the answer's caveats together |
| safe student measurement sheet | one item/measurement row | makes a bed, desk, or cabinet dimension independently retrievable and citable |
| policy | heading-aware paragraphs, 350-850 Chinese characters | preserves conditions, exceptions, article numbers, and page citations |
| service manual | numbered steps grouped within a page | lets the answer composer assemble an ordered procedure |
| form | resource entity, not factual passage | a blank form is something to open or submit, not a rule |
| link | navigation resource | lets the product offer a next action without treating link metadata as policy |
| image/scanned PDF | no assertable chunk before OCR | prevents filenames and captions from masquerading as source text |
| record spreadsheet | privacy quarantine | avoids indexing names, student numbers, scores, or participation records |

A global fixed-token splitter is specifically rejected. Chinese policy
conditions frequently depend on the preceding article or the next exception;
FAQ answers depend on their questions; procedure steps depend on their order.

## Retrieval

1. Apply hard applicability filters such as undergraduate vs graduate.
2. Retrieve exact terminology with SQLite FTS5 over Chinese unigrams,
   bigrams, trigrams, titles, headings, and tags.
3. Retrieve an offline second candidate list with hashed character subword
   vectors and a small domain synonym map.
4. Fuse candidate ranks with reciprocal-rank fusion.
5. Rerank primarily with question/rule-anchor similarity, concept and attribute
   coverage, source-type fit, and applicability. Authority is only a near-tie
   signal; it does not multiplicatively demote student experience.
6. Produce an answerability state before calling a model.
7. When the answer plan routes to the web, fetch only registered HTTPS hosts,
   then recompute answerability using the retrieved `official_web` and/or
   `public_web` evidence.
8. Before adopting a passage, compare it with the question's required aspects.
   A lexical match on a background attribute does not satisfy a missing answer
   object. Rank direct coverage before authority, then use authority and
   freshness to qualify the supported claim.
9. When a search provider is configured, execute discovery for planned web
   routes, deduplicate discovered URLs with registry results, and apply the same
   evidence contract. Missing credentials produce registry-only behavior rather
   than a false claim that open-web search ran.

Retrieval relevance and answer trust are separate concerns. Student-authored
guides and measurements may rank first when they directly answer a lifestyle
question, while the answer layer must label them as student experience. Formal
policy questions still prefer policy text because that source type matches the
question, not because all non-official material is globally suppressed.

The offline vector is a testable baseline, not the production semantic model.
Its interface should later be replaced by multilingual embeddings. Keep FTS5:
exact terms such as document numbers, system names, form names, course types,
and dates are often stronger than semantic similarity.

## Answerability states

- `supported`: current official evidence is present.
- `supported_freshness_unverified`: official guidance is relevant but its
  publication/effective date is not verified.
- `supported_with_context`: official evidence plus non-conflicting peer
  context is available.
- `mixed_sources_review_required`: official and peer evidence coexist and a
  top passage contains uncertainty.
- `experience_only`: only student experience supports the response.
- `web_supported`: a retrieved official webpage supports the answer.
- `public_web_supported`: a retrieved public source supports general context,
  not school policy.
- `web_supported_mixed`: official and public live evidence jointly support
  clearly separated claims.
- `supported_with_unresolved_wording`: a source confirms which credential is
  awarded, but no source directly confirms its wording or appearance.
- `insufficient_official_evidence`: the question asks for a rule or procedure
  but no official evidence is available.
- `unverified` / `insufficient`: the system must not generate a factual answer.

## Why not a knowledge graph first

A graph becomes valuable later for deadlines, dependencies, offices, forms,
and personalized task plans. It does not solve paragraph retrieval, policy
citations, or source freshness. The sensible evolution is:

```text
document index → structured action records → task/dependency graph
```

The MVP therefore leaves room for an `actions` table without forcing all prose
into triples prematurely.
