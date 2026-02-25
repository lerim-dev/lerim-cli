---
id: learning-queue-atomic-claims
title: Queue claim operations must be atomic
created: 2026-02-19T14:30:00Z
updated: 2026-02-19T14:30:00Z
source: sync-20260219-143000-def456
confidence: 0.9
tags:
  - queue
  - concurrency
  - debugging
kind: pitfall
---

Race condition in queue consumer caused duplicate processing. The fix was using SELECT FOR UPDATE (or CAS) for the claim step. This friction wasted 2 hours of debugging. Always use atomic operations for queue claims.
