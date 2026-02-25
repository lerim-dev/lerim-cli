---
id: learning-retry-idempotency-a
title: Add idempotency keys to prevent duplicate webhook deliveries
created: 2026-02-18T16:00:00Z
updated: 2026-02-18T16:00:00Z
source: sync-20260218-160000-dup001
confidence: 0.8
tags:
  - webhooks
  - retry
  - idempotency
kind: pitfall
---

Retry logic caused duplicate webhook deliveries. Fixed by adding UUID-based idempotency keys to all webhook payloads.
