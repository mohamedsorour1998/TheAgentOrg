# Ticket: CLEAN-1 — Add a per-IP login rate limit

**Owner of this file: Reem.**

## Description
Add a Redis-backed counter, keyed on the client IP, that blocks more than five
login attempts per minute and returns HTTP 429.

## Acceptance criteria
- Six requests in one minute from one IP returns 429.
- Five requests in one minute still return 200.
- Redis connection details are read from the environment (`REDIS_URL`).
- No credentials are committed to the repository.

This is the "happy path" ticket: the developer agent produces a clean diff, the
scanners find nothing blocking, and the change is promoted.
