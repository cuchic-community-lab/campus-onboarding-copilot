# Contributing

This project combines an independently maintained student knowledge source with
a retrieval and grounded-generation application. Contributions must preserve
both provenance and technical accountability.

## Workflow

1. Open or claim an issue describing the user problem and acceptance criteria.
2. Create a short-lived branch from `main` using `feat/`, `fix/`, `content/`,
   `eval/`, or `docs/`.
3. Keep content changes separate from retrieval/model changes when practical.
4. Add a regression case for every retrieval or grounding defect.
5. Run `make test`, `make evaluate`, `make evaluate-chat`, and
   `make publication-audit` before requesting review.
6. Open a pull request and complete the evidence, risk, and validation sections.
7. Require at least one review from the other maintainer before merging.

Direct pushes to `main` are not part of the collaboration workflow.

## Review ownership

- Knowledge-source, provenance, and content-lifecycle changes require review
  from the knowledge-base maintainer.
- Retrieval, grounding, evaluation, and model-provider changes require review
  from the RAG maintainer.
- Source-policy, privacy, public-release, and licensing changes require both.

The final GitHub usernames will be activated in `.github/CODEOWNERS` after both
maintainers confirm their accounts and ownership agreement.

## Evidence boundaries

- Do not present student experience as an official school rule.
- Preserve year, cohort, campus, major, source URL, and uncertainty.
- Do not silently correct source text; document curation decisions.
- Do not commit raw synchronized files, generated indexes, API keys, personal
  records, QR-group data, or private conversations.
- Do not claim an accuracy metric without a defined labeled evaluation set.

## Definition of done

A pull request is ready only when its user-visible behavior, tests, evaluation
impact, privacy implications, and fallback behavior are documented. Screenshots
are useful for UI changes, but reproducible tests remain required.
