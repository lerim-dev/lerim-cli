---
name: Use JWT with HS256 for authentication
description: JWT with HS256 for single-service auth instead of session cookies
type: project
---

We decided to use JWT tokens with HS256 signing for authentication instead of session cookies. HS256 is simpler for a single-service architecture. The signing secret is stored in environment variables.
