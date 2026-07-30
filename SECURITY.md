# Security policy

## Reporting

Do not open a public issue for leaked credentials, exposed student records,
unsafe source documents, or a vulnerability that could reveal private data.
Use GitHub's private vulnerability reporting or contact the maintainers through
an agreed private channel.

## Secret handling

- Keep model keys in `.env.local` for local development and in the deployment
  platform's secret manager for hosted environments.
- Never place a provider key in browser JavaScript, screenshots, issues, pull
  requests, logs, or committed fixtures.
- Rotate a key immediately if its full value is exposed.

## Data handling

Only intentionally public, non-sensitive source material may enter the normal
ingestion path. Record-style spreadsheets, student identifiers, private group
content, and unreviewed submissions must remain quarantined until an explicit
privacy review approves their use.
