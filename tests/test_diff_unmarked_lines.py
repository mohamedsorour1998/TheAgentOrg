"""A line the model forgot to mark is still in the change, and the scanners must read it.

MEASURED 2026-09-25 with the real model and the real scanners, demo checkbox OFF: a
ticket that pasted an AWS key produced a diff whose new function had no `+` markers,
the key inside it as an `os.environ.get` fallback. `added_files` kept only `+` lines,
so gitleaks was handed a file without the key and security answered PASS with
`scan_provenance: scanners`. `open_pr` commits the diff text itself, key included.

The diff below is that model output's shape, shortened. The key is AWS's published
documentation example, the same one the demo's poisoned ticket uses.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentorg.common import diff

REPO_ROOT = Path(__file__).resolve().parent.parent

MODEL_DIFF = """--- a/app/auth.py
+++ b/app/auth.py
@@ -1,11 +1,20 @@
 from flask import request, jsonify
+import boto3
+import os

 def authenticate(username: str, password: str) -> bool:
     return bool(username) and bool(password)

def log_failed_login(username: str):
    key_id = os.environ.get('AWS_ACCESS_KEY_ID', 'AKIAIOSFODNN7EXAMPLE')
    s3 = boto3.client('s3', aws_access_key_id=key_id)
\\ No newline at end of file
"""


def test_an_unmarked_line_carrying_a_key_is_part_of_the_change():
    body = diff.added_files(MODEL_DIFF)["app/auth.py"]
    assert "AKIAIOSFODNN7EXAMPLE" in body, (
        "the key sits on a line with no `+` marker; dropping it hands the scanners a "
        "file without it and security passes a change that ships a credential"
    )


def test_blank_and_meta_lines_are_not_added():
    lines = diff.added_files(MODEL_DIFF)["app/auth.py"].split("\n")
    assert "" not in lines, "a blank line that lost its context space was counted as added"
    assert not any(line.startswith(("\\", "@@", "---")) for line in lines)
    # The two marked lines come first and in order, so marked numbering is untouched.
    assert lines[:2] == ["import boto3", "import os"]
    # A context line BEFORE the unmarked run is still context: the run starts at the
    # first unmarked line, not at the top of the hunk.
    assert not any("def authenticate" in line for line in lines)


def test_the_discriminator_pair_does_not_move():
    """`{3, 4}` is the real-scanner line pair, a property of this exact reference diff."""
    reference = json.loads((REPO_ROOT / "fixtures" / "dev_result_poisoned.json").read_text())["diff"]
    lines = diff.added_files(reference)["app/auth.py"].split("\n")
    assert "AKIAIOSFODNN7EXAMPLE" in lines[2], lines[:5]
    assert "wJalrXUtnFEMI" in lines[3], lines[:5]
