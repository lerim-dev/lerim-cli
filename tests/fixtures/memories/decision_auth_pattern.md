---
id: decision-jwt-hs256-auth
title: Use JWT with HS256 for authentication
created: 2026-02-20T10:01:05Z
updated: 2026-02-20T10:01:05Z
source: sync-20260220-100100-abc123
confidence: 0.85
tags:
  - auth
  - jwt
  - security
---

We decided to use JWT tokens with HS256 signing for authentication instead of session cookies. HS256 is simpler for a single-service architecture. The signing secret is stored in environment variables.
