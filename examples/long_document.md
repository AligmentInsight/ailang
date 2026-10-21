# Architecture note: caching layer, 2026-Q3

This document describes the caching layer we introduced for the public API
and the trade-offs we accepted. It is deliberately long: the digest agent
is supposed to compress it into a typed artifact.

## Why a cache at all

Our read traffic grew 4x in two quarters while write traffic stayed flat.
Ninety-one percent of reads touched fewer than 0.3% of the keys. A shared
cache in front of the database removed most of that load: p99 latency fell
from 240 ms to 61 ms and database CPU dropped by half.

## What we built

We placed a Redis-compatible cache in the request path. Keys are namespaced
by tenant and object type. Invalidation is write-through: every mutation
publishes an event, and the cache drops the affected keys. TTLs are a
safety net (five minutes), not the primary strategy.

## Risks we accepted

1. Cold-start stampede: after a deploy, many requests miss at once. We
   mitigate with jittered TTLs and a single-flight lock per key.
2. Stale reads across regions: replication lag up to 400 ms can serve a
   value that was just replaced elsewhere.
3. Operational coupling: the cache is now a hard dependency. If it is
   down, the API degrades to a rate-limited pass-through, not full outage.

## Open questions

Should tenant-level invalidation be a first-class operation? Do we need a
per-endpoint cache policy instead of one global TTL? Both questions are
deferred to the next quarter.
