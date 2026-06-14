'use strict';

const config = require('./config');

async function fetchWithTimeout(url, opts, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...opts, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function fetchOnce() {
  const res = await fetchWithTimeout(
    config.rssUrl,
    {
      headers: {
        'User-Agent': config.userAgent,
        Accept: 'application/rss+xml, application/xml, text/xml, */*',
      },
    },
    8000,
  );

  if (!res.ok) {
    throw new Error(`Upstream returned HTTP ${res.status} for ${config.rssUrl}`);
  }

  const body = await res.text();
  const head = body.trimStart().slice(0, 5);
  if (head[0] !== '<') {
    throw new Error('Upstream did not return XML');
  }
  return body;
}

async function fetchRSS() {
  let lastErr;
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      return await fetchOnce();
    } catch (err) {
      lastErr = err;
      if (attempt === 2) break;
      await new Promise((r) => setTimeout(r, 500));
    }
  }
  throw lastErr;
}

module.exports = { fetchRSS };
