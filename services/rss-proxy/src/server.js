'use strict';

const express = require('express');
const config = require('./config');
const { TTLCache } = require('./cache');
const { fetchRSS } = require('./fetcher');

const cache = new TTLCache(config.cacheTtlSeconds);
let lastError = null;

const app = express();
app.disable('x-powered-by');
app.use(express.json());

app.get('/health', (_req, res) => {
  const c = cache.get();
  const age = c ? c.ageSeconds : null;
  const ok = c !== null && age !== null && age < config.cacheTtlSeconds * 2;
  res.status(ok ? 200 : 503).json({
    status: ok ? 'ok' : 'degraded',
    upstream: config.rssUrl,
    lastFetch: c ? new Date(c.fetchedAt).toISOString() : null,
    ageSeconds: age,
    cacheFresh: c ? c.fresh : null,
    cacheTtlSeconds: config.cacheTtlSeconds,
    lastError: lastError ? String(lastError) : null,
  });
});

app.get('/rss.xml', async (_req, res) => {
  const existing = cache.get();

  if (existing && existing.fresh) {
    res.set('X-Cache', 'HIT');
    res.type('application/rss+xml; charset=utf-8');
    return res.send(existing.value);
  }

  try {
    const body = await fetchRSS();
    cache.set(body);
    lastError = null;
    res.set('X-Cache', existing ? 'STALE->FRESH' : 'MISS');
    res.set('Cache-Control', 'public, max-age=300');
    res.type('application/rss+xml; charset=utf-8');
    return res.send(body);
  } catch (err) {
    lastError = err.message || String(err);
    if (existing && existing.value) {
      res.set('X-Cache', 'STALE');
      res.set('Warning', '110 - "Response is Stale"');
      res.type('application/rss+xml; charset=utf-8');
      return res.status(200).send(existing.value);
    }
    return res.status(502).json({
      error: 'upstream_unavailable',
      message: lastError,
    });
  }
});

app.post('/refresh', (req, res) => {
  if (!config.refreshSecret) {
    return res.status(503).json({ error: 'refresh_disabled', message: 'REFRESH_SECRET not configured' });
  }
  const headerToken = req.get('X-Refresh-Token');
  const bodyToken = req.body && req.body.token;
  const token = headerToken || bodyToken;
  if (token !== config.refreshSecret) {
    return res.status(401).json({ error: 'unauthorized' });
  }
  cache.invalidate();
  return res.json({ ok: true, invalidated: true, note: 'next /rss.xml call will refetch' });
});

app.get('/', (_req, res) => {
  const c = cache.get();
  res.type('text/plain').send(
    [
      'rss-proxy',
      `upstream: ${config.rssUrl}`,
      `cache: ${c ? (c.fresh ? 'fresh' : 'stale') : 'empty'}`,
      `ageSeconds: ${c ? c.ageSeconds : 'n/a'}`,
      '',
      'endpoints:',
      '  GET  /rss.xml',
      '  GET  /health',
      '  POST /refresh   (X-Refresh-Token: <REFRESH_SECRET>)',
    ].join('\n'),
  );
});

app.listen(config.port, () => {
  // eslint-disable-next-line no-console
  console.log(
    `rss-proxy listening on :${config.port} ` +
      `(upstream=${config.rssUrl}, ttl=${config.cacheTtlSeconds}s, env=${config.nodeEnv})`,
  );
});
