"""CORPUS 3: CVE and remediation context, so the security agent's PROSE is specific. H4.

THE NAMED PURPOSE, from spec §10(c). Read the emphasis: PROSE. This corpus feeds
`SecurityResult.explanation`, which is written by `agents/security.py:_explain` AFTER
`compute_security_verdict` has already decided, from a verdict passed in as an argument. The
reply is assigned to `explanation` and is never parsed. So this is the one corpus whose
consumer is anywhere near the security stage, and it is the corpus most in need of the
boundary `guard.py` enforces -- its consumer is spelled `security_explanation`, not
`security`, and there is no name that reaches the rule.

WHAT THIS CORPUS MUST NOT CONTAIN, and the constraint shapes every entry below:

    NO SEVERITIES. Not one entry states or suggests a severity, a CVSS score, a threshold,
    or whether a finding blocks. Severity comes from `agentorg/security/scoring.py` -- ONE
    table for three scanners -- and gitleaks' `critical` is a POLICY: any finding from a
    secret scanner is critical, because a committed credential has no lesser grade. A corpus
    entry offering a severity would be a second declaration of that policy, retrievable by
    a model, and the two would drift while both looked authoritative.

    NO FALSE-POSITIVE GUIDANCE. Nothing here says a finding might be benign, expected, a
    test fixture, or safe in context. That is the exact shape of the attack: a document that
    argues a finding away, retrieved into the stage that explains a block.
    `tests/test_retrieval_boundary.py` feeds documents of precisely that shape through the
    real rule and asserts the verdict does not move -- but the corpus itself should not
    supply the ammunition.

WHAT AN ENTRY IS: a rule name, what an attacker does with a hit on it, and the remediation.
That makes the explanation specific -- "an AWS access key id was committed; anyone who can
read this repository's history can call the AWS API as this account until the key is
revoked" -- instead of restating the finding. Nothing in it is a judgement about whether to
block.

ADVISORY IDS ARE ILLUSTRATIVE AND SAY SO. This corpus does not query a live CVE feed --
that is a network call from inside `agentorg/`, which is what conftest guard 6 exists to
prevent, and an offline corpus is the only kind a hermetic test can measure. Where an entry
names a CVE it is a real published identifier, cited so it can be checked; the corpus does
not claim to be current, and a stale advisory corpus that presented itself as current would
be the mislabelled-metric failure again.
"""

from __future__ import annotations

from .search import Document

NAME = "advisories"

DOCUMENTS: list[Document] = [
    Document(
        doc_id="advisory-0001",
        title="A committed AWS access key id is usable by anyone who can read the history",
        body=(
            "gitleaks rule aws-access-key-id matches the AKIA-prefixed identifier. Paired "
            "with its secret it authenticates AWS API calls as that principal, and rewriting "
            "the commit does not revoke it -- forks, clones, CI caches and the push event "
            "itself already carry it. Remediation: deactivate and delete the key in IAM "
            "first, then remove it from the source and read it from the environment or an "
            "instance role."
        ),
        source="AWS IAM key rotation guidance; gitleaks rule aws-access-key-id",
        keywords=(
            "aws", "access", "key", "akia", "aws-access-key-id", "iam", "rotate",
            "revoke", "gitleaks", "credential",
        ),
    ),
    Document(
        doc_id="advisory-0002",
        title="A committed AWS secret access key cannot be un-published",
        body=(
            "gitleaks rule aws-secret-access-key matches the 40-character secret. It is the "
            "half that signs requests, so its exposure is complete on first push. "
            "Remediation is identical and in the same order: revoke in IAM, then edit the "
            "source. There is no code change that makes a published secret safe."
        ),
        source="gitleaks rule aws-secret-access-key",
        keywords=(
            "aws", "secret", "access", "key", "aws-secret-access-key", "sign",
            "revoke", "gitleaks", "credential",
        ),
    ),
    Document(
        doc_id="advisory-0003",
        title="Hardcoded credentials in source: the remediation is environment or a secret store",
        body=(
            "Any credential literal in tracked source -- a password, an API token, a private "
            "key, a connection string with a password in it -- is readable by everyone with "
            "repository access and by every system that mirrors it. Remediation: read it "
            "from an environment variable or a managed secret store at process start, and "
            "keep the literal out of version control entirely rather than out of the current "
            "revision."
        ),
        source="OWASP A07:2021 Identification and Authentication Failures",
        keywords=(
            "hardcoded", "credential", "password", "token", "private", "connection",
            "string", "environment", "secret", "store", "owasp",
        ),
    ),
    Document(
        doc_id="advisory-0004",
        title="String-built SQL: parameterise; escaping is not a remediation",
        body=(
            "Building a query by concatenating or interpolating a request value lets an "
            "attacker change the query's structure, not just its data -- so it reads and "
            "writes whatever the database user can. Remediation: parameterised queries or "
            "bound statements, so the value can never be parsed as SQL. Escaping helpers "
            "and allow-list filters are defence in depth, not a substitute."
        ),
        source="OWASP A03:2021 Injection",
        keywords=(
            "sql", "injection", "query", "parameterised", "parameterized", "bind",
            "concatenate", "format", "escape", "owasp",
        ),
    ),
    Document(
        doc_id="advisory-0005",
        title="Shell invocation from request data: pass an argument list, not a string",
        body=(
            "Invoking a shell with a command string assembled from request data lets an "
            "attacker append their own commands with a separator. Remediation: pass an "
            "argument vector with no shell, and validate the value against an allow-list "
            "before it ever reaches the call."
        ),
        source="OWASP A03:2021 Injection; CWE-78",
        keywords=(
            "shell", "command", "injection", "subprocess", "exec", "system",
            "argument", "cwe-78", "os",
        ),
    ),
    Document(
        doc_id="advisory-0006",
        title="Unpinned dependencies: a build is not reproducible and a compromise is silent",
        body=(
            "A floating version range resolves differently over time, so the artifact "
            "demonstrated in August is not the artifact built in September, and a "
            "compromised release inside the range is installed without any change to the "
            "source. Remediation: pin exact versions and record them in a lock file; review "
            "a bump as a change."
        ),
        source="trivy dependency findings; supply-chain guidance",
        keywords=(
            "dependency", "dependencies", "version", "pin", "pinned", "lock",
            "requirements", "trivy", "supply", "chain", "upgrade",
        ),
    ),
    Document(
        doc_id="advisory-0007",
        title="CVE-2021-44228 (Log4Shell): why logging an unvalidated request value matters",
        body=(
            "A published example of a logging path becoming remote code execution: Apache "
            "Log4j 2 resolved JNDI lookups inside logged strings, so an attacker-supplied "
            "header reached a remote class loader. The general lesson for a remediation note "
            "is that a logging call is an interpreter of its input unless it is proven not "
            "to be. Cited as a real advisory identifier so it can be checked; this corpus is "
            "offline and does not claim to be current."
        ),
        source="CVE-2021-44228, published 2021-12-10",
        keywords=(
            "cve", "log4j", "log4shell", "jndi", "logging", "rce", "remote", "code",
            "execution", "header",
        ),
    ),
    Document(
        doc_id="advisory-0008",
        title="Login endpoints without rate limiting: credential stuffing is the named attack",
        body=(
            "An unthrottled authentication endpoint lets an attacker replay a breached "
            "credential list at machine speed, so the cost of an attempt approaches zero. "
            "Remediation: limit attempts per account and per source, return HTTP 429 past "
            "the threshold, and keep the counter in a store that survives a process restart "
            "if the limit is meant to hold across one."
        ),
        source="OWASP A07:2021; NIST SP 800-63B rate-limiting guidance",
        keywords=(
            "rate", "limit", "login", "authentication", "credential", "stuffing",
            "brute", "force", "429", "throttle", "attempts",
        ),
    ),
]
