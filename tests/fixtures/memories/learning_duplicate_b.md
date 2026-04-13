---
name: Webhook retries cause duplicates without idempotency keys
description: Webhook retry mechanism sends duplicate payloads without idempotency keys
type: feedback
---

Discovered that webhook retry mechanism was sending duplicate payloads. Solution: add idempotency keys (UUIDs) to every webhook call so receivers can deduplicate.
