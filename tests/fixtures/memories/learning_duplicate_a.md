---
name: Add idempotency keys to prevent duplicate webhook deliveries
description: Retry logic caused duplicate webhook deliveries; fixed with UUID idempotency keys
type: feedback
---

Retry logic caused duplicate webhook deliveries. Fixed by adding UUID-based idempotency keys to all webhook payloads.
