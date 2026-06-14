'use strict';

class TTLCache {
  constructor(ttlSeconds) {
    this.ttlMs = ttlSeconds * 1000;
    this.value = null;
    this.fetchedAt = null;
  }

  get() {
    if (this.value === null || this.fetchedAt === null) return null;
    return {
      value: this.value,
      fetchedAt: this.fetchedAt,
      fresh: this.isFresh(),
      ageSeconds: this.ageSeconds(),
    };
  }

  isFresh() {
    if (this.fetchedAt === null) return false;
    return (Date.now() - this.fetchedAt) < this.ttlMs;
  }

  set(value) {
    this.value = value;
    this.fetchedAt = Date.now();
  }

  invalidate() {
    this.value = null;
    this.fetchedAt = null;
  }

  ageSeconds() {
    if (this.fetchedAt === null) return null;
    return Math.floor((Date.now() - this.fetchedAt) / 1000);
  }
}

module.exports = { TTLCache };
