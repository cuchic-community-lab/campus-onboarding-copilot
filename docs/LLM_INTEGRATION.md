# LLM integration contract

## Boundary

The model is an answer composer, not the knowledge base and not the retrieval
judge. `/api/context` is the integration boundary.

```text
user question + student profile
  → retrieval + applicability filters
  → source policy + answerability decision
  → versioned context packet
  → LLM answer composer
  → citation and policy validator
```

The model receives only the query, system rules, answerability state, and
ranked evidence objects. It does not receive arbitrary raw files and should not
be asked to search the database itself.

## Context packet

```json
{
  "query": "宿舍是几人间？",
  "can_generate": true,
  "response_mode": "experience_only",
  "model_contract_version": "campus-grounding-v1",
  "system_rules": ["..."],
  "evidence": [
    {
      "evidence_id": "S1",
      "authority_tier": "peer_experience",
      "assertion_policy": "label_as_experience",
      "published_at": null,
      "uploaded_at": "2026-07-24",
      "student_level": "undergraduate",
      "uncertainty": ["大概率", "推测", "可能"],
      "text": "..."
    }
  ]
}
```

## Implemented provider interface

The application uses one provider-neutral method:

```python
class AnswerComposer(Protocol):
    def generate(self, context_packet: dict) -> dict:
        """Return answer, citations, and unresolved questions."""
```

Provider configuration belongs in environment variables or a secrets manager,
not the repository. `OpenAICompatibleComposer` uses only the standard library,
and `ExtractiveComposer` is the no-key fallback. The response uses a structured
schema:

```json
{
  "answer": "...",
  "citations": ["S1", "S2"],
  "claims": [
    {"text": "...", "evidence_ids": ["S1"], "certainty": "experience"}
  ],
  "unresolved": ["等待学院公布2026级宿舍分配"]
}
```

Makers Models is available as the `CAMPUS_LLM_PROVIDER=makers` preset. It uses
the EdgeOne OpenAI-compatible gateway and defaults to the built-in
`@makers/deepseek-v4-flash` model. The API key lives only in ignored
`.env.local` during local development. Model responses request JSON mode; a
string or missing `unresolved` field is normalized to a list, while citations,
claims, authority labels, and refusal behavior remain subject to strict
validation.

## Post-generation validator

Before returning an answer:

1. every citation must exist in the packet;
2. each policy claim must cite an assertable official source;
3. peer evidence must be labeled as experience;
4. uncertainty markers may not disappear;
5. dates and student applicability must match the evidence;
6. `can_generate=false` permits only a refusal plus a request for the missing
   source.

The current validator implements citation existence, inline citation presence,
claim-to-evidence linkage, official-source requirements, peer-experience
labeling, and refusal compliance. Full natural-language entailment checking is
not yet implemented and must not be implied by the current validator.

## Conversation boundary

The session store retains at most four turns in memory. Explicit follow-ups may
reuse the previous user question to form the retrieval query, while the answer
still has to cite evidence retrieved on the current turn. Conversation text is
never treated as factual evidence. Sessions disappear when the process restarts.

## Rollout

1. Collect failed questions and create human relevance labels.
2. Replace the local subword baseline and compare retrieval ablations.
3. Add claim-level entailment and temporal-conflict evaluation.
4. Persist isolated sessions only when account and retention rules exist.
5. Add structured student actions only after the factual answer path is
   reliable.

This order prevents fluent model output from concealing weak retrieval.
