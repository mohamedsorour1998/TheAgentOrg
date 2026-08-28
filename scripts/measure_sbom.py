#!/usr/bin/env python
"""SBOM for the agent container, plus the scanner-update process. Owner: Lane L.

    .venv-main/bin/python scripts/measure_sbom.py

Writes `docs/final/evidence/sbom.json` (CycloneDX 1.5) and
`docs/final/evidence/sbom.md` (the readable form, with the update process).
Answers the supply-chain half of specification §5.

--------------------------------------------------------------------------------
WHAT THIS SBOM IS, AND -- MORE IMPORTANTLY -- WHAT IT IS NOT
--------------------------------------------------------------------------------

It is a SOURCE SBOM: everything the image is DECLARED to contain, read from the
files that declare it. It is not an image scan.

The distinction is the whole honesty of the artifact and it must not be blurred:

  WHAT THIS SEES          the five pinned Python packages, the three scanner
                          binaries and their versions, the base image reference,
                          and every direct dependency's pin tightness
  WHAT THIS CANNOT SEE    the transitive closure actually resolved at build time,
                          the base image's own OS packages, and the digest of the
                          image that is deployed

Why it cannot: the image is `linux/arm64` and is built by CodeBuild from ECR
Public. Producing a true image SBOM needs the built image, which needs either a
Docker daemon that can build arm64 or a pull of the deployed tag -- neither of
which exists on the machine this script is written to run on, and neither of which
should be a hidden prerequisite of an evidence script. `syft` is also absent here
(checked, not assumed).

So the script reports what it can verify and NAMES the command a human runs to get
the rest. A source SBOM presented as an image SBOM would be the exact defect this
repository is built around: a check that cannot tell "did not run" from "passed".

--------------------------------------------------------------------------------
WHY CYCLONEDX AND NOT A TABLE
--------------------------------------------------------------------------------

A security product asked for an SBOM is being asked for a machine-readable one. A
markdown table is what a human reads, so both are written: the JSON is the
artifact and the markdown is the briefing. `bom-ref` values are purl strings so a
consumer can join this against a vulnerability feed without a name-matching step.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = REPO_ROOT / "docs" / "final" / "evidence"

DOCKERFILE = REPO_ROOT / "agentorg" / "agents" / "Dockerfile"
REQUIREMENTS = REPO_ROOT / "agentorg" / "agents" / "requirements.txt"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT = REPO_ROOT / "pyproject.toml"

# The three scanner binaries, with where each comes from. The download URL is part
# of the supply chain -- a pinned version fetched over an unpinned channel is only
# half a pin -- so the host is recorded per tool.
SCANNER_SOURCES = {
    "gitleaks": {
        "arg": "GITLEAKS_VERSION",
        "supplier": "github.com/gitleaks/gitleaks",
        "delivery": "release tarball, linux_arm64",
        "purl_type": "generic",
    },
    "trivy": {
        "arg": "TRIVY_VERSION",
        "supplier": "github.com/aquasecurity/trivy",
        "delivery": "release tarball, Linux-ARM64",
        "purl_type": "generic",
    },
    "semgrep": {
        "arg": "SEMGREP_VERSION",
        "supplier": "PyPI",
        "delivery": "pip, into its own venv at /opt/semgrep-venv",
        "purl_type": "pypi",
    },
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _base_image() -> dict:
    """The FROM line, parsed. The base is a dependency like any other.

    Recorded WITHOUT a digest, and that gap is stated rather than papered over: the
    Dockerfile pins a tag (`3.12-slim`), and a tag is mutable. Pinning the digest
    would make the base immutable; it would also mean a security update to the base
    requires a repository edit, which is a real tradeoff rather than an oversight.
    """
    text = _read(DOCKERFILE)
    match = re.search(r"^FROM\s+(?:--platform=(\S+)\s+)?(\S+)", text, re.MULTILINE)
    if not match:
        return {"error": "no FROM line found in the Dockerfile"}
    platform_arg, reference = match.group(1), match.group(2)
    name, _, tag = reference.rpartition(":")
    return {
        "reference": reference,
        "name": name or reference,
        "tag": tag,
        "platform": platform_arg or "(unset)",
        "registry": reference.split("/")[0],
        "pinned_by": "tag" if "@sha256:" not in reference else "digest",
        "note": (
            "ECR Public, not Docker Hub: CodeBuild pulls anonymously and Docker Hub "
            "answers 429 late in the build. Pinned by TAG, so the base can change "
            "under the same reference -- an accepted tradeoff, because pinning the "
            "digest means a base security update needs a commit here."
        ),
    }


def _python_packages() -> list[dict]:
    """The five pinned runtime packages, read from requirements.txt.

    EXACT pins throughout, which is the file's stated intent ("Pinned, not
    floating"). Each entry records the pin operator so a future `>=` creeping in is
    visible in the SBOM rather than only in a diff.
    """
    packages = []
    for line in _read(REQUIREMENTS).splitlines():
        stripped = line.split("#")[0].strip()
        if not stripped:
            continue
        match = re.match(r"^([A-Za-z0-9._-]+)\s*(==|>=|<=|~=|>|<)?\s*(.*)$", stripped)
        if not match:
            continue
        name, operator, version = match.groups()
        packages.append({
            "name": name,
            "version": version or "",
            "operator": operator or "",
            "pin": "EXACT" if operator == "==" else "NOT EXACT",
            "purl": f"pkg:pypi/{name.lower()}@{version}" if version else f"pkg:pypi/{name.lower()}",
        })
    return packages


def _declared_but_unpinned() -> dict:
    """pyproject's runtime list, which is what a `pip install .` resolves.

    THIS IS THE GAP WORTH KNOWING. The image installs `requirements.txt` first and
    then `pip install --no-deps .`, so the exact pins win inside the container. But
    a developer running `pip install -e .[dev]` gets pyproject's list, which is
    largely unpinned -- so the environment the suite runs in locally is NOT the
    environment the image ships. That is not a defect to fix here; it is a fact an
    SBOM reader must be told, because a vulnerability report against one does not
    describe the other.
    """
    text = _read(PYPROJECT)
    block = re.search(r"dependencies = \[(.*?)\]", text, re.DOTALL)
    entries = []
    if block:
        for raw in block.group(1).splitlines():
            stripped = raw.strip()
            commented = stripped.startswith("#")
            spec = stripped.lstrip("#").strip().strip('",').strip('"')
            if not spec:
                continue
            has_floor = ">" in spec
            has_ceiling = "<" in spec
            entries.append({
                "spec": spec,
                "commented_out": commented,
                "pin": (
                    "EXACT" if "==" in spec
                    else "BOUNDED" if has_floor and has_ceiling
                    else "FLOOR_ONLY" if has_floor
                    else "UNPINNED"
                ),
            })
    return {
        "entries": entries,
        "note": (
            "the IMAGE installs requirements.txt (exact) then `pip install "
            "--no-deps .`, so these floating specifiers do not reach the "
            "container. They DO reach a local `pip install -e .[dev]`, so the "
            "development environment and the shipped image are not the same "
            "closure. Report a CVE against whichever you measured."
        ),
    }


def _scanner_components() -> dict:
    """The three binaries, their pinned versions, and image<->CI agreement.

    The agreement matters for a reason beyond tidiness: CI is what the block rule's
    expected findings were measured against, so an image carrying a different
    gitleaks could report different line numbers -- and the line numbers are the
    only field separating a real scan from the fixture.
    """
    dockerfile_text = _read(DOCKERFILE)
    ci_text = _read(CI_WORKFLOW)

    components = {}
    for tool, meta in SCANNER_SOURCES.items():
        arg = meta["arg"]
        image_match = re.search(rf"ARG {arg}=([\d.]+)", dockerfile_text)
        ci_match = re.search(rf"{arg}:\s*\"?([\d.]+)\"?", ci_text)
        image_version = image_match.group(1) if image_match else None
        ci_version = ci_match.group(1) if ci_match else None

        purl = (
            f"pkg:pypi/semgrep@{image_version}"
            if meta["purl_type"] == "pypi"
            else f"pkg:generic/{tool}@{image_version}"
        )
        components[tool] = {
            "image_version": image_version,
            "ci_version": ci_version,
            "agree": image_version == ci_version and image_version is not None,
            "supplier": meta["supplier"],
            "delivery": meta["delivery"],
            "purl": purl,
            "on_this_machine": _local_scanner_version(tool),
        }
    return components


def _local_scanner_version(tool: str) -> str | None:
    """What version is on THIS PATH, if any.

    Reported because a local measurement made with a different scanner version than
    the image carries is a different measurement. `REAL_SCANNER_LINES` is a property
    of gitleaks 8.21.2 AND one exact diff -- so a mismatch here is the first thing
    to suspect when the discriminator moves.
    """
    binary = shutil.which(tool)
    if binary is None:
        return None
    flag = {"gitleaks": "version", "trivy": "--version", "semgrep": "--version"}[tool]
    try:
        result = subprocess.run(
            [binary, flag], capture_output=True, text=True, check=False, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "present, but would not report a version"
    output = (result.stdout or result.stderr).strip()
    found = re.search(r"(\d+\.\d+\.\d+)", output)
    return found.group(1) if found else output.splitlines()[0][:60] if output else None


def _os_packages() -> dict:
    """The apt packages the Dockerfile installs by name.

    Deliberately UNVERSIONED in the Dockerfile (`apt-get install -y curl
    ca-certificates git`), so the version is whatever the base image's index offers
    on build day. Recorded as a known gap with its blast radius rather than as a
    component with a fabricated version.
    """
    text = _read(DOCKERFILE)
    match = re.search(r"apt-get install[^\\\n]*--no-install-recommends([^\\\n]*)", text)
    names = match.group(1).split() if match else []
    return {
        "packages": names,
        "versioned": False,
        "note": (
            "installed by name with no version, so the exact build differs per "
            "build date. `git` is required at runtime -- github_ops.open_pr shells "
            "out to it on the offline path -- and `ca-certificates` is what makes "
            "every HTTPS fetch in the image verifiable. Pinning apt versions would "
            "need a snapshot mirror; not this phase."
        ),
    }


def _what_this_cannot_see() -> list[dict]:
    """The gaps, each with the command that closes it.

    A gap with a command is a task; a gap without one is an excuse. Every entry
    here names a real command, not a suggestion to "investigate".
    """
    return [
        {
            "gap": "the transitive dependency closure actually installed",
            "why": (
                "requirements.txt pins five DIRECT packages. strands-agents and "
                "boto3 pull dozens of transitive ones, and their versions are "
                "resolved at build time by pip, not declared anywhere in this repo."
            ),
            "command": (
                "docker run --rm --platform linux/arm64 "
                "<account>.dkr.ecr.us-east-1.amazonaws.com/theagentorg-shared-"
                "security-agent:latest pip freeze"
            ),
        },
        {
            "gap": "the base image's own OS package inventory",
            "why": "python:3.12-slim ships a Debian userland this repo never lists.",
            "command": "syft public.ecr.aws/docker/library/python:3.12-slim -o cyclonedx-json",
        },
        {
            "gap": "the digest of the image actually deployed",
            "why": (
                "five ECR tags are deployed and a tag is mutable. Without a digest "
                "an SBOM describes an intention rather than an artifact."
            ),
            "command": (
                "aws ecr describe-images --repository-name "
                "theagentorg-shared-security-agent --region us-east-1 "
                "--query 'imageDetails[].imageDigest'"
            ),
        },
        {
            "gap": "known vulnerabilities in any of the above",
            "why": (
                "an SBOM is an inventory, not an assessment. trivy is already in "
                "the image and can scan the image it is in."
            ),
            "command": "trivy image --format cyclonedx <image reference>",
        },
    ]


# The scanner-update process. Written as DATA rather than prose in a markdown file,
# so `tests/test_evidence.py` can assert the steps exist and are ordered -- a
# process kept only in prose drifts from what anyone actually does.
SCANNER_UPDATE_PROCESS = [
    {
        "step": 1,
        "action": "Bump the version in BOTH places, in one commit",
        "detail": (
            "agentorg/agents/Dockerfile (ARG <TOOL>_VERSION) and "
            ".github/workflows/ci.yml (env <TOOL>_VERSION). They are two "
            "declarations of one fact; a commit that moves one is a build whose "
            "scanner differs from the one the suite was gated against."
        ),
        "verify": ".venv-main/bin/python scripts/measure_sbom.py  # agree: true",
    },
    {
        "step": 2,
        "action": "Re-measure the block rule's expected findings",
        "detail": (
            "scripts/scan_gate.py carries EXPECTED_BLOCKING, measured on gitleaks "
            "8.21.2. A scanner update can move a reported LINE NUMBER, and the line "
            "numbers are the only field separating a real scan from the fixture."
        ),
        "verify": ".venv-main/bin/python scripts/scan_gate.py",
    },
    {
        "step": 3,
        "action": "Re-measure the provenance discriminator",
        "detail": (
            "tests/provenance.py's REAL_SCANNER_LINES is {3, 4} and FIXTURE_LINES is "
            "{4, 5}. If an update moves the real set onto {4, 5} the discriminator "
            "is GONE and every provenance assertion keeps passing while proving "
            "nothing. The sets must stay distinct -- that is the acceptance test for "
            "a scanner bump, not a green suite."
        ),
        "verify": ".venv-main/bin/python -m pytest tests/test_provenance.py -q",
    },
    {
        "step": 4,
        "action": "Rebuild the image and read the version tail",
        "detail": (
            "the Dockerfile ends its scanner layer with `gitleaks version && trivy "
            "--version && semgrep --version`. That tail catches a binary that "
            "downloads but cannot execute -- a BROKEN scanner, which blocks every "
            "run including the clean one, and is a different fault from an absent "
            "one."
        ),
        "verify": "the deploy workflow's build step; failure is the intended outcome",
    },
    {
        "step": 5,
        "action": "Re-run preflight against the deployed runtime",
        "detail": (
            "check 3 invokes the security runtime and asserts the real line "
            "numbers. It is the only check that distinguishes a real scan from the "
            "fixture, and a runtime reports READY before its endpoint serves the "
            "new version -- so a green deploy is not evidence."
        ),
        "verify": ".venv-main/bin/python scripts/preflight.py",
    },
]


def _cyclonedx(report: dict) -> dict:
    """CycloneDX 1.5, so a consumer can join this against a vulnerability feed.

    `metadata.properties` carries the honesty: an SBOM consumer that treats this as
    an image SBOM would under-report, so the document says what it is in a field
    rather than only in the markdown beside it.
    """
    components = []

    base = report["base_image"]
    if "reference" in base:
        components.append({
            "type": "container",
            "bom-ref": f"pkg:docker/{base['name']}@{base['tag']}",
            "name": base["name"],
            "version": base["tag"],
            "purl": f"pkg:docker/{base['name']}@{base['tag']}",
            "properties": [
                {"name": "agentorg:pinned_by", "value": base["pinned_by"]},
                {"name": "agentorg:platform", "value": base["platform"]},
            ],
        })

    for package in report["python_packages"]:
        components.append({
            "type": "library",
            "bom-ref": package["purl"],
            "name": package["name"],
            "version": package["version"],
            "purl": package["purl"],
            "properties": [{"name": "agentorg:pin", "value": package["pin"]}],
        })

    for tool, meta in report["scanners"].items():
        components.append({
            "type": "application",
            "bom-ref": meta["purl"],
            "name": tool,
            "version": meta["image_version"] or "",
            "purl": meta["purl"],
            "supplier": {"name": meta["supplier"]},
            "properties": [
                {"name": "agentorg:delivery", "value": meta["delivery"]},
                {"name": "agentorg:ci_agrees", "value": str(meta["agree"]).lower()},
            ],
        })

    for name in report["os_packages"]["packages"]:
        components.append({
            "type": "library",
            "bom-ref": f"pkg:deb/debian/{name}",
            "name": name,
            "version": "",
            "purl": f"pkg:deb/debian/{name}",
            "properties": [
                {"name": "agentorg:versioned", "value": "false"},
            ],
        })

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "timestamp": report["measured_at"],
            "component": {
                "type": "container",
                "bom-ref": "theagentorg-agent-image",
                "name": "theagentorg-agent",
                "version": report["commit"],
            },
            "properties": [
                {
                    "name": "agentorg:sbom_kind",
                    "value": "source-declared",
                },
                {
                    "name": "agentorg:not_an_image_scan",
                    "value": (
                        "declared components only. The transitive closure, the "
                        "base image's OS inventory and the deployed digest are "
                        "NOT here -- see gaps[] in sbom.json for the command that "
                        "produces each."
                    ),
                },
                {"name": "agentorg:commit", "value": report["commit"]},
            ],
        },
        "components": components,
    }


def _markdown(report: dict) -> str:
    lines: list[str] = []
    add = lines.append

    add("# SBOM — the agent container")
    add("")
    add(
        f"Generated by `scripts/measure_sbom.py` at commit `{report['commit']}`, "
        f"{report['measured_at']}. Machine-readable form: `sbom.json` (CycloneDX 1.5)."
    )
    add("")
    add("**This is a source-declared SBOM, not an image scan.** It lists what the")
    add("image is declared to contain, read from the files that declare it. The")
    add("transitive closure pip resolves at build time, the base image's own Debian")
    add("packages, and the digest of the deployed tag are **not** in here. Each of")
    add("those gaps is listed at the end with the command that closes it.")
    add("")
    add("## Base image")
    add("")
    base = report["base_image"]
    add("| Field | Value |")
    add("|---|---|")
    add(f"| reference | `{base.get('reference', '?')}` |")
    add(f"| platform | `{base.get('platform', '?')}` |")
    add(f"| registry | `{base.get('registry', '?')}` |")
    add(f"| pinned by | **{base.get('pinned_by', '?')}** |")
    add("")
    add(base.get("note", ""))
    add("")

    add("## Python packages — the image's five, all exact")
    add("")
    add("| Package | Version | Pin |")
    add("|---|---|---|")
    for package in report["python_packages"]:
        add(f"| `{package['name']}` | `{package['version']}` | {package['pin']} |")
    add("")
    add(
        "These are installed from `agentorg/agents/requirements.txt` **before** "
        "`pip install --no-deps .`, so they are what the container runs."
    )
    add("")

    add("## What a local `pip install -e .[dev]` gets instead")
    add("")
    add("| Specifier | Pin |")
    add("|---|---|")
    for entry in report["declared_dependencies"]["entries"]:
        note = " (commented out)" if entry["commented_out"] else ""
        add(f"| `{entry['spec']}`{note} | {entry['pin']} |")
    add("")
    add(report["declared_dependencies"]["note"])
    add("")

    add("## The three scanners")
    add("")
    add("| Tool | Image | CI | Agree | This machine | Delivery |")
    add("|---|---|---|---|---|---|")
    for tool, meta in report["scanners"].items():
        add(
            f"| `{tool}` | `{meta['image_version']}` | `{meta['ci_version']}` | "
            f"{'yes' if meta['agree'] else '**NO**'} | "
            f"`{meta['on_this_machine'] or 'absent'}` | {meta['delivery']} |"
        )
    add("")
    add(
        "The image and CI columns must agree. CI is what the block rule's expected "
        "findings were measured against, so an image carrying a different `gitleaks` "
        "can report different line numbers — and the line numbers are the only field "
        "separating a real scan from the fixture."
    )
    add("")
    add(
        "Only the **security** image needs these three, and it is the only one that "
        "gets `SCANNERS_REQUIRED=true`. All five runtimes share one image, so all "
        "five carry the binaries; only one is allowed to demand them."
    )
    add("")

    add("## OS packages")
    add("")
    os_packages = report["os_packages"]
    add(f"`{'`, `'.join(os_packages['packages'])}` — installed by name, **unversioned**.")
    add("")
    add(os_packages["note"])
    add("")

    add("## The scanner-update process")
    add("")
    add(
        "A scanner bump is not a version edit. It can move a reported line number, "
        "and this repository's entire verification story rests on two line-number "
        "sets staying distinct. Five steps, in order:"
    )
    add("")
    for step in report["update_process"]:
        add(f"**{step['step']}. {step['action']}**")
        add("")
        add(step["detail"])
        add("")
        add(f"```\n{step['verify']}\n```")
        add("")

    add("## What this SBOM cannot see")
    add("")
    for gap in report["gaps"]:
        add(f"**{gap['gap']}**")
        add("")
        add(gap["why"])
        add("")
        add(f"```\n{gap['command']}\n```")
        add("")

    return "\n".join(lines)


def _git_head() -> str:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() or "unknown"


def measure() -> dict:
    return {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "commit": _git_head(),
        "conditions": {
            "sbom_kind": "source-declared, NOT an image scan",
            "syft_available": shutil.which("syft") is not None,
            "docker_available": shutil.which("docker") is not None,
            "python": platform.python_version(),
            "note": (
                "the image is linux/arm64 and built by CodeBuild. A true image "
                "SBOM needs the built image; the gaps section names the command."
            ),
        },
        "base_image": _base_image(),
        "python_packages": _python_packages(),
        "declared_dependencies": _declared_but_unpinned(),
        "scanners": _scanner_components(),
        "os_packages": _os_packages(),
        "update_process": SCANNER_UPDATE_PROCESS,
        "gaps": _what_this_cannot_see(),
    }


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", type=Path, default=EVIDENCE / "sbom.json",
        help="where to write the CycloneDX document",
    )
    parser.add_argument(
        "--markdown", type=Path, default=None,
        help="where to write the readable form (default: alongside --out, as .md)",
    )
    args = parser.parse_args(argv)

    report = measure()
    document = _cyclonedx(report)
    # The gaps and the update process travel in the JSON too, not only in the
    # markdown: a consumer reading the machine-readable file must be able to learn
    # that it is not an image scan without being handed the prose beside it.
    document["_agentorg"] = {
        "commit": report["commit"],
        "measured_at": report["measured_at"],
        "conditions": report["conditions"],
        "gaps": report["gaps"],
        "update_process": report["update_process"],
        "scanners": report["scanners"],
        "declared_dependencies": report["declared_dependencies"],
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    markdown_path = args.markdown or args.out.with_suffix(".md")
    markdown_path.write_text(_markdown(report) + "\n", encoding="utf-8")

    print(f"SBOM  commit {report['commit']}  {report['measured_at']}")
    print(f"  kind:       {report['conditions']['sbom_kind']}")
    print(f"  components: {len(document['components'])}")
    print(f"  base:       {report['base_image'].get('reference')} "
          f"(pinned by {report['base_image'].get('pinned_by')})")
    print(f"  packages:   {len(report['python_packages'])} exact-pinned")
    for tool, meta in report["scanners"].items():
        mark = "agree" if meta["agree"] else "DISAGREE"
        print(
            f"  {tool:<10}  image={meta['image_version']} ci={meta['ci_version']} "
            f"{mark}  local={meta['on_this_machine'] or 'absent'}"
        )
    print(f"  gaps:       {len(report['gaps'])}, each with a command")
    print(f"  update:     {len(report['update_process'])} steps")
    print()
    print(f"wrote {_display_path(args.out)}")
    print(f"wrote {_display_path(markdown_path)}")

    disagreements = [
        tool for tool, meta in report["scanners"].items() if not meta["agree"]
    ]
    if disagreements:
        print(
            f"FAIL: the image and CI disagree on {disagreements}. CI is what the "
            f"block rule's expected findings were measured against.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
