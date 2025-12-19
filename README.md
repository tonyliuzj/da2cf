# da2cf

Web app to sync DNS records from DirectAdmin (source of truth) to Cloudflare (destination).

## Features

- FastAPI web UI with Basic login (session cookies).
- DirectAdmin ➜ Cloudflare DNS sync in mirror mode.
- Multiple domains with per-domain enable/disable and scheduling.
- Global proxy policy for A/AAAA/CNAME records.
- Plan preview (dry-run) and per-run history with counts.
- SQLite persistence via SQLModel.
- APScheduler-based auto sync (Europe/London timezone).

## Configuration

Create a `.env` file in the project root (or set equivalent environment variables). The app automatically loads this file on startup:

```env
APP_ADMIN_USER=admin
APP_ADMIN_PASSWORD=changeme
APP_SECRET_KEY=change_this_secret

DIRECTADMIN_BASE_URL=https://da.example.com:2222
DIRECTADMIN_USERNAME=dauser
DIRECTADMIN_PASSWORD=dapassword

CLOUDFLARE_EMAIL=you@example.com
CLOUDFLARE_GLOBAL_API_KEY=cf_global_key

DEFAULT_SYNC_INTERVAL_MINUTES=15
MANAGED_RECORD_TYPES=A,AAAA,CNAME,TXT,MX,SRV,CAA
EXCLUDE_NAMES=
ACME_SYNC_INTERVAL_MINUTES=5
SYNC_CONCURRENCY=5

PROXY_A=true
PROXY_AAAA=true
PROXY_CNAME=true
PROXY_HOST=true
PROXY_SUB=true
```

Secrets (passwords, keys) are never rendered in the UI or stored in history.

## Running locally

Install dependencies (Poetry is used here, but you can adapt to your tooling):

```bash
poetry install
poetry run uvicorn da2cf.main:app --reload
```

Then open `http://localhost:8000` and log in with `APP_ADMIN_USER` / `APP_ADMIN_PASSWORD`.

## Docker

Build and run using Docker Compose:

```bash
docker-compose up --build
```

The app listens on port `8000`.

## Tests

Run the test suite with:

```bash
poetry run pytest
```
