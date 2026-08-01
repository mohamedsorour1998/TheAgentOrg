# target_repo/ — the app the agents modify

**Owner: Reem.**

This is a tiny Flask app that the Agent Org's developer agent changes. It is
deliberately small — just enough to have a real file (`app/auth.py`) for a diff
to touch and for the scanners to scan.

You do NOT need to know Strands, AWS, or the pipeline to build this. It is a
plain Python app plus two ticket files.

## What to build (see docs/plan/reem.md)

- `app/auth.py` — a minimal Flask login handler (a few lines).
- `tests/test_auth.py` — one or two tests so CI has something to run.
- The two tickets live in `../tickets/` (clean + poisoned).

The poisoned ticket must, on its own, trip a scanner — verify with Habiba by
**Wed Aug 12** (she needs it for her scanner lane).
