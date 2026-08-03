# Tencent Cloud beta deployment

This deployment is intended for a small student beta on one Tencent Cloud
Lighthouse or CVM instance. It keeps the application stateless except for a
named Docker volume containing private RAG traces and opt-in human handoffs.

## Before deployment

- Use an Ubuntu 24.04 LTS instance with at least 2 vCPU and 2 GB memory.
- Point an ICP-filed domain or subdomain at the instance public IP.
- Allow inbound TCP 80 and 443 and UDP 443. Restrict SSH 22 to the maintainer's
  current IP whenever practical.
- Install Docker Engine with the Compose plugin from Docker's official
  repository.
- Keep the GitHub repository private until code and corpus redistribution
  boundaries are settled.

Do not expose port 8000 in the Tencent Cloud firewall. Caddy is the only public
entry point and obtains and renews HTTPS certificates automatically after DNS
and filing are ready.

## Configure and start

Clone the repository on the server, check out the reviewed release commit, then:

```bash
cp deploy/.env.production.example deploy/.env.production
chmod 600 deploy/.env.production
```

Edit only `deploy/.env.production` on the server. Set the real domain and the
Makers API key. Do not paste secrets into GitHub, browser JavaScript, screenshots,
issues, or chat messages.

Validate and start:

```bash
docker compose --env-file deploy/.env.production config
docker compose --env-file deploy/.env.production build
docker compose --env-file deploy/.env.production up -d
docker compose ps
curl --fail https://YOUR_DOMAIN/api/health
```

The first build creates the knowledge index from the reviewed tracked corpus.
The application container runs as a non-root user. Query traces and submitted
emails persist in the `hic_runtime` volume when the image or container changes.
Caddy access logging is intentionally disabled so the deployment does not build
an unnecessary second store of student IP addresses. Container error and
lifecycle logs remain available to operators.

## Operate

View service logs without printing the environment file:

```bash
docker compose logs --tail 200 app
docker compose logs --tail 200 caddy
```

Inspect real browser traces and pending human follow-ups:

```bash
docker compose exec app campus-copilot traces list \
  --environment production --source browser --limit 50
docker compose exec app campus-copilot handoffs list --limit 50
```

Upgrade only from a reviewed `main` commit:

```bash
git pull --ff-only origin main
docker compose --env-file deploy/.env.production build
docker compose --env-file deploy/.env.production up -d
curl --fail https://YOUR_DOMAIN/api/health
```

Before an upgrade, back up the runtime database with SQLite's online backup API:

```bash
docker compose exec app python -c "import sqlite3; source=sqlite3.connect('/app/data/runtime/rag_traces.db'); target=sqlite3.connect('/app/data/runtime/rag_traces.backup.db'); source.backup(target); target.close(); source.close()"
docker compose cp app:/app/data/runtime/rag_traces.backup.db ./rag_traces.backup.db
```

Store that backup in an encrypted maintainer-controlled location. It contains
student queries and optional plaintext email addresses. Never commit or serve it.

## Release gate

Before sharing the URL beyond a small beta:

1. Verify the domain's ICP status and HTTPS certificate.
2. Publish a short privacy notice covering query traces, optional email use,
   30-day retention, maintainer access, and deletion contact.
3. Run the unit tests, retrieval and chat evaluations, and publication audit.
4. Test the live site on desktop and a real phone.
5. Submit several unsupported questions and confirm the handoff queue works.
6. Confirm Makers billing alerts and API-key restrictions.

This single-instance SQLite architecture is appropriate for the initial beta.
Move operational records to a managed database only when traffic, concurrent
writes, account isolation, or multi-instance deployment creates a measured need.
