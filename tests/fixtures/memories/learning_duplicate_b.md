---
id: learning-webhook-dedup-b
title: Webhook retries cause duplicates without idempotency keys
created: 2026-02-19T11:00:00Z
updated: 2026-02-19T11:00:00Z
source: sync-20260219-110000-dup002
confidence: 0.75
tags:
  - webhooks
  - retry
  - deduplication
kind: friction
---

Discovered that webhook retry mechanism was sending duplicate payloads. Solution: add idempotency keys (UUIDs) to every webhook call so receivers can deduplicate.
