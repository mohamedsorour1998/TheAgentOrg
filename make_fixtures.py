"""Generates fixtures/*.json and validates every one against state.py."""

import json
import pathlib

from agentorg.state import (
    DevResult,
    PlanResult,
    ReviewResult,
    SecurityResult,
    SREResult,
    Finding,
    compute_security_verdict,
)

OUT = pathlib.Path("fixtures")
OUT.mkdir(exist_ok=True)

POISONED_DIFF = '''--- a/app/auth.py
+++ b/app/auth.py
@@ -1,5 +1,21 @@
 from flask import request, jsonify
+import redis
+
+AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
+AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
+
+_rate_store = redis.Redis(host="cache.internal", port=6379)
+
+
+def _rate_limited(ip):
+    key = "login:" + ip
+    hits = _rate_store.incr(key)
+    if hits == 1:
+        _rate_store.expire(key, 60)
+    return hits > 5
 
 
 def login():
+    if _rate_limited(request.remote_addr):
+        return jsonify({"error": "too many attempts"}), 429
     user = authenticate(request.form["username"], request.form["password"])
'''

CLEAN_DIFF = '''--- a/app/auth.py
+++ b/app/auth.py
@@ -1,5 +1,21 @@
+import os
+
 from flask import request, jsonify
+import redis
+
+_rate_store = redis.Redis.from_url(os.environ["REDIS_URL"])
+
+
+def _rate_limited(ip):
+    key = "login:" + ip
+    hits = _rate_store.incr(key)
+    if hits == 1:
+        _rate_store.expire(key, 60)
+    return hits > 5
 
 
 def login():
+    if _rate_limited(request.remote_addr):
+        return jsonify({"error": "too many attempts"}), 429
     user = authenticate(request.form["username"], request.form["password"])
'''

plan = PlanResult(
    tasks=[
        "Add a Redis-backed counter keyed on client IP",
        "Return HTTP 429 once the threshold is passed",
        "Add tests for the under-limit and over-limit cases",
    ],
    acceptance_criteria=[
        "Six requests in one minute from one IP returns 429",
        "Five requests in one minute still return 200",
        "No credentials are committed to the repository",
    ],
    target_files=["app/auth.py", "tests/test_auth.py"],
    notes="Redis connection details must come from the environment.",
)

dev_poisoned = DevResult(
    branch="feat/login-rate-limit",
    diff=POISONED_DIFF,
    summary="Adds a per-IP rate limit of five login attempts per minute.",
    files_changed=["app/auth.py"],
    pr_url="https://github.com/mohamedsorour1998/auth-service/pull/41",
)

dev_clean = DevResult(
    branch="feat/login-rate-limit",
    diff=CLEAN_DIFF,
    summary="Adds a per-IP rate limit of five login attempts per minute. "
            "Redis connection now read from REDIS_URL.",
    files_changed=["app/auth.py"],
    pr_url="https://github.com/mohamedsorour1998/auth-service/pull/41",
)

review = ReviewResult(
    verdict="approve",
    comments=[{"file": "app/auth.py", "line": 12, "note": "Counter expiry looks right."}],
    must_fix=[],
)

findings = [
    Finding(
        tool="gitleaks",
        severity="critical",
        rule="aws-access-key-id",
        file="app/auth.py",
        line=4,
        description="AWS access key id committed in source.",
    ),
    Finding(
        tool="gitleaks",
        severity="critical",
        rule="aws-secret-access-key",
        file="app/auth.py",
        line=5,
        description="AWS secret access key committed in source.",
    ),
    Finding(
        tool="semgrep",
        severity="low",
        rule="python.flask.missing-timeout",
        file="app/auth.py",
        line=7,
        description="Redis client created without a socket timeout.",
    ),
]

verdict, blocking = compute_security_verdict(findings, threshold="high")
security_block = SecurityResult(
    verdict=verdict,
    findings=findings,
    blocking=blocking,
    explanation="Two AWS credentials are hardcoded in app/auth.py. "
                "Move them to the environment and rotate the key before merging.",
)

security_pass = SecurityResult(
    verdict="pass",
    findings=[findings[2]],
    blocking=[],
    explanation="One low-severity finding, below the block threshold.",
)

sre = SREResult(
    verdict="go",
    ci_status="passing",
    slo_checks=[
        {"name": "unit tests", "passed": True, "detail": "42 passed"},
        {"name": "error budget", "passed": True, "detail": "97% remaining"},
    ],
    estimated_cost_note="No new infrastructure. Reuses the existing Redis instance.",
    notes="Rollback is a revert of the single commit.",
)

files = {
    "plan_result.json": plan,
    "dev_result_poisoned.json": dev_poisoned,
    "dev_result_clean.json": dev_clean,
    "review_result.json": review,
    "security_result_block.json": security_block,
    "security_result_pass.json": security_pass,
    "sre_result.json": sre,
}

for name, model in files.items():
    (OUT / name).write_text(json.dumps(model.model_dump(), indent=2) + "\n")

# Read every file back and re-validate, so a broken fixture fails loudly.
models = {
    "plan_result.json": PlanResult,
    "dev_result_poisoned.json": DevResult,
    "dev_result_clean.json": DevResult,
    "review_result.json": ReviewResult,
    "security_result_block.json": SecurityResult,
    "security_result_pass.json": SecurityResult,
    "sre_result.json": SREResult,
}
for name, cls in models.items():
    cls.model_validate_json((OUT / name).read_text())
    print(f"ok  {name}")

print(f"\nblock rule check: verdict={security_block.verdict}, "
      f"blocking findings={len(security_block.blocking)}")
