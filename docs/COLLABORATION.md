# Collaboration and ownership

## Current contribution boundary

| Area | Primary origin or owner | Review expectation |
|---|---|---|
| HIC onboarding source site and original knowledge collection | Zihuanana | Knowledge maintainer review |
| Retrieval, ranking, grounded generation, evaluation, and Makers integration | Pengwei Fu | RAG maintainer review |
| Source authority, privacy, public release, and product direction | Shared | Both maintainers |

Git history and pull requests are the evidence for subsequent ownership. This
table must be updated when responsibilities change; it is not a substitute for
a license or a legal agreement.

## Repository relationship

The recommended long-term layout keeps the source knowledge project and this
Copilot application in separate repositories. The application synchronizes a
versioned public manifest, records hashes and provenance, and builds local
indexes. It does not make a second repository the authority for the original
content.

## Before public release

Both maintainers must confirm:

1. GitHub organization, repository name, and exact usernames;
2. permission to index, display excerpts from, and redistribute each source;
3. separate licenses for application code and knowledge content;
4. project naming and an explicit non-official-university disclaimer;
5. domain, deployment, secret, and cloud-cost ownership;
6. how each maintainer may describe their contribution publicly.

Until those decisions are recorded, create the remote repository as private.
