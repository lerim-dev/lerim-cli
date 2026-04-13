---
name: Queue claim operations must be atomic
description: Race condition in queue consumer caused duplicate processing; use atomic claims
type: feedback
---

Race condition in queue consumer caused duplicate processing. The fix was using SELECT FOR UPDATE (or CAS) for the claim step. This friction wasted 2 hours of debugging. Always use atomic operations for queue claims.
