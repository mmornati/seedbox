'use strict';

function readInt(name, fallback) {
  const v = process.env[name];
  if (v === undefined || v === '') return fallback;
  const n = parseInt(v, 10);
  return Number.isFinite(n) ? n : fallback;
}

module.exports = {
  port: readInt('PORT', 3000),
  rssUrl: process.env.RSS_URL || 'https://blog.mornati.net/rss.xml',
  cacheTtlSeconds: readInt('CACHE_TTL_SECONDS', 3600),
  refreshSecret: process.env.REFRESH_SECRET || '',
  nodeEnv: process.env.NODE_ENV || 'development',
  userAgent: 'rss-proxy-vps/1.0 (+https://rss.pygame.ovh)',
};
