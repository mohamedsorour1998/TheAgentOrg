# Habiba — Week 3 (Aug 22–27): harden + hand off

Feature freeze **Tuesday Aug 25**. After that: only fix what dry runs find.

---

## Sat–Sun Aug 22–23 — fail-safe edge cases

**Task: handle scanner missing, scanner times out, scanner returns malformed
JSON — fail safe (surface an error finding, don't crash the graph).**
```python
try:
    result = subprocess.run([...], timeout=30, capture_output=True)
except (FileNotFoundError, subprocess.TimeoutExpired):
    return [Finding(severity="high", tool="gitleaks",
                     description="scanner unavailable — treated as unknown risk")]
```
**Done when:** killing a scanner mid-run doesn't take down the pipeline. Pair
this with Aya's `test_chaos_*.py` — she asserts the pipeline handles it, you
make it actually handle it.

---

## Mon Aug 24 — speed

**Task: cache scanner results for the fixed demo diffs** so the live run is
fast (hash the diff, cache the finding list).
**Done when:** a demo run returns findings in under a second.

---

## Tue Aug 25 — freeze

**Task: from freeze onward, only fix what dry runs surface.** No new work.

---

## Wed–Thu Aug 26–27 — final verification

**Task: run the full demo (clean + poisoned) a few more times alongside the
team's dry runs**, confirming nothing regressed after the fail-safe changes.
**Done when:** poisoned still blocks 10/10, clean still passes, and a killed
scanner mid-run degrades gracefully instead of crashing.

---

## End of week 3 — done when

- A missing/timing-out/malformed scanner produces a safe finding instead of
  crashing the graph.
- Demo runs return findings in under a second (cached).
- Final verification alongside the team's dry runs is clean.
