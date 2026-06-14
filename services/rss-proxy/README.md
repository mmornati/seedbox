# rss-proxy

A tiny Node.js / Express service that fetches an upstream RSS feed on a long
TTL and serves it from cache. Designed to bypass Cloudflare bot/rate-limit
challenges by serving the feed from a stable origin (a self-hosted Traefik
endpoint) instead of letting GitHub Actions hit `blog.mornati.net/rss.xml`
directly from CI runners.

This service is wired into the seedbox (`mmornati/seedbox`) so that it is
exposed at `rss.${TRAEFIK_DOMAIN}` (e.g. `rss.mornati.ovh`) through the
seedbox's existing Traefik reverse proxy + automatic Let's Encrypt.

## How it fits in the seedbox

The seedbox's `run-seedbox.sh` reads `config.yaml` and generates Traefik
labels for every enabled service. As long as `rss-proxy` is enabled there
and the env vars below are set in `.env`, running `./run-seedbox.sh`
rebuilds the image, starts the container, and exposes it on
`https://rss.${TRAEFIK_DOMAIN}` with HTTPS handled by Traefik.

## Configuration

All configuration is via environment variables (no files, no volumes):

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `RSS_URL` | yes | — | Upstream RSS feed to fetch and cache. |
| `CACHE_TTL_SECONDS` | no | `3600` | How long a cached payload stays fresh (1h by default). |
| `PORT` | no | `3000` | HTTP listen port (the seedbox config assumes 3000). |
| `REFRESH_SECRET` | yes (in prod) | — | Shared secret for the `/refresh` endpoint. Generate with `openssl rand -hex 32`. |
| `NODE_ENV` | no | `production` | Standard Node flag. |
| `TZ` | no | host TZ | Used only for log timestamps. |

In the seedbox these are mapped from the `RSS_PROXY_*` variables in `.env`
(`RSS_PROXY_RSS_URL`, `RSS_PROXY_CACHE_TTL_SECONDS`,
`RSS_PROXY_REFRESH_SECRET`) — see `.env.sample`.

## Endpoints

- `GET /rss.xml` — returns the cached feed (`application/rss+xml`,
  `charset=utf-8`). Sends `X-Cache: HIT|MISS|STALE` and, on STALE,
  `Warning: 110 - "Response is stale"`.
- `GET /health` — `200` when the cache is healthy, `503` when the cache is
  empty AND the last upstream fetch failed. Body is JSON with details.
- `GET /refresh?token=…` — force-refreshes the cache. Requires
  `?token=<REFRESH_SECRET>`. Returns `200` on success, `401` otherwise.
- `GET /` — short usage blurb.

## Failure modes

- **Cold start with upstream down** — `/rss.xml` returns `502`; `/health`
  returns `503` with `lastError` populated.
- **Upstream fails after a successful fetch** — `/rss.xml` keeps serving
  the last good payload with `X-Cache: STALE` and `Warning: 110`. `/health`
  stays `200` but reports `lastError` and a `degraded` flag.

## Local development

```bash
cd services/rss-proxy
cp .env.example .env   # edit RSS_URL and REFRESH_SECRET
docker build -t rss-proxy:dev .
docker run --rm -p 3000:3000 --env-file .env rss-proxy:dev
curl http://localhost:3000/rss.xml
curl http://localhost:3000/health
```

## Why not just call `RSS_URL` from GitHub Actions?

Cloudflare aggressively rate-limits requests coming from CI egress IPs
(returning HTTP 429 / 503 with a JavaScript challenge page), which breaks
the `actions/rss` based blog post workflow. Serving the feed from a
self-hosted origin under a custom domain (`rss.mornati.ovh`) keeps the
upstream request rate low and the response surface stable, so CI
fetchers always get a clean RSS payload.
