"""Did this run reach AWS? Answered by INTERCEPTION, never by assertion.

THE CLAIM THIS MODULE EXISTS TO REFUSE. "A self-hosted run makes no AWS call" is
the kind of statement this repository has been burned by: on 2026-08-22 every
agent in the deployed pipeline was serving fixtures while every job reported
green, and the check positioned to catch it grepped for a string both paths
produce. A green run proves the run finished. It proves nothing about where the
bytes went.

Three ways to answer the question, and only the third is evidence:

  1. Unset the credentials and observe success.   REFUTED BELOW.
  2. Read the code and reason that no call is made. A claim about the source, not
     about the run -- and `llm.text()` catches every exception by design, so a
     call that was ATTEMPTED and denied looks identical to one never made.
  3. Intercept every outbound connection and record the hosts. THIS MODULE.

WHY (1) IS NOT ENOUGH, MEASURED. `llm.available()` reads boto3 credentials, so
with none present it returns False and every agent goes to its fixture -- the run
then makes no AWS call AND does no model work, and the two facts are
indistinguishable in the result. Worse in the other direction: this machine holds
credentials in `~/.aws/credentials`, so a run that "looked local" could have been
talking to Bedrock the whole time. Absence of credentials is not evidence of
absence of a call; it removes the ability to make one AND the ability to tell.

THE INTERCEPT IS AT `socket.socket.connect`, and the position is the whole point.
Above it -- in botocore, in strands, in `llm._complete` -- a witness measures our
own wrapper and would miss anything that reached the network another way. Below it
there is no Python. Every HTTP client in this dependency closure (botocore's
urllib3, httpx, requests, ollama's client) ends at this one method, so a witness
here cannot be routed around by application code.

WHAT IT DOES NOT PROVE, stated because an over-claim here would be the same
defect one level up:

  - It observes THIS PROCESS. A subprocess -- `git clone`, a scanner binary --
    has its own socket module and is invisible here. `subprocess_note()` says so.
  - It observes CONNECTIONS, not payloads. A connection to a non-AWS host that
    proxies to Bedrock would pass. Nothing on a laptop distinguishes those.
  - A hostname is resolved to an IP before `connect` for some clients, so the
    hosts recorded are what the caller ASKED for. An IP-literal connection to an
    AWS endpoint is recorded as an IP and matched against no marker.

So the honest form of the claim is: *no connection to any AWS-owned hostname was
attempted from this process during the run.* That is narrower than "no AWS call"
and it is what the evidence supports.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

# Substring markers for AWS-owned hostnames. SUBSTRINGS rather than exact hosts
# because the endpoint set is large and versioned -- `bedrock-runtime.us-east-1`,
# `bedrock-agentcore.us-east-1`, `sts`, `dynamodb`, `secretsmanager`, each with a
# regional prefix this module must not have to enumerate.
#
# `.amazonaws.com` alone would be the obvious single marker and is NOT sufficient:
# `amazonaws.com.cn` serves the China partition and `api.aws` is the newer
# dual-stack suffix, so a run reaching either would be recorded as clean. All
# three are listed, and a test asserts the set is non-empty -- a matcher over an
# empty marker list finds nothing and reports every run as air-gapped, which is
# this repository's signature failure shape (a check that cannot distinguish "did
# not run" from "passed").
AWS_HOST_MARKERS: tuple[str, ...] = (
    "amazonaws.com",
    "amazonaws.com.cn",
    "api.aws",
)


class ContactedAWS(RuntimeError):
    """Raised the instant a run reaches an AWS host under `strict=True`.

    RAISES RATHER THAN RECORDING, and the choice is deliberate. A witness that
    only tallied would let a demo finish and put the finding in a report nobody
    reads before the projector goes on. Under `strict` the first AWS connection
    ends the run at the call site that made it, so the traceback names the code
    responsible -- which is the one piece of information a tally cannot give.

    The default is NON-strict, because the primary use is measuring a run that is
    expected to be clean, and an exception there would replace a measurement with
    an outage. `scripts/selfhost_demo.py` runs strict; the parity harness does not.
    """


@dataclass
class NetworkWitness:
    """Every host this process asked to connect to, and whether any was AWS.

    Not frozen: it accumulates during the run it observes. The lists are the
    evidence, so they are kept in full rather than counted -- a count cannot be
    audited afterwards, and "which host" is the first question anyone asks.
    """

    hosts: list[str] = field(default_factory=list)
    aws_hosts: list[str] = field(default_factory=list)
    #: True once the witness has been installed and removed. A witness that was
    #: never installed has empty lists, which reads EXACTLY like a clean run --
    #: the distinction `scan_provenance` exists for, one layer over. Never infer
    #: air-gap from empty lists; read this flag first.
    observed: bool = False

    def is_airgapped_from_aws(self) -> bool:
        """True when the witness ran AND recorded no AWS host.

        THE `observed` CLAUSE IS THE POINT. Without it this returns True for a
        witness nobody installed, so the most reassuring answer would be the one
        produced by doing nothing at all.
        """
        return self.observed and not self.aws_hosts

    def summary(self) -> str:
        """One line for a log or a slide. Names the unobserved case explicitly."""
        if not self.observed:
            return "network: NOT OBSERVED -- no witness was installed, so this " \
                   "run's outbound connections are unknown"
        if self.aws_hosts:
            unique = sorted(set(self.aws_hosts))
            return (f"network: REACHED AWS -- {len(self.aws_hosts)} connection(s) "
                    f"to {len(unique)} AWS host(s): {', '.join(unique)}")
        return (f"network: no AWS host contacted -- {len(self.hosts)} outbound "
                f"connection(s) to {len(set(self.hosts))} distinct host(s)")

    def subprocess_note(self) -> str:
        """The limit of this evidence, in the artifact rather than in a docstring.

        A caveat that lives only in source is one nobody quoting the number will
        read. This string is rendered beside the summary wherever the witness is
        reported, for the same reason `POLICY["gitleaks"].rationale` is rendered
        into the PR comment instead of staying a comment.
        """
        return ("scope: this process only. A child process (git, a scanner) has "
                "its own socket module and is not observed here.")


def _host_of(address: object) -> str:
    """The host a `connect()` address tuple names, or "" for a non-IP family.

    AF_UNIX addresses are plain strings and AF_INET6 tuples carry four elements;
    both reach this function, so the shape is checked rather than assumed. A
    unix-socket connect is not a network call and must not be recorded as one --
    the docker daemon socket is exactly that, and recording it would put a
    non-network host in evidence about the network.
    """
    if isinstance(address, tuple) and address and isinstance(address[0], str):
        return address[0]
    return ""


def _is_aws(host: str) -> bool:
    """True when `host` names an AWS endpoint by any known suffix."""
    lowered = host.lower()
    return any(marker in lowered for marker in AWS_HOST_MARKERS)


@contextmanager
def witness(*, strict: bool = False) -> Iterator[NetworkWitness]:
    """Record every outbound connection this process attempts.

    Patches `socket.socket.connect` for the duration and restores the original in
    a `finally`, so an exception inside the block cannot leave the interceptor
    installed. A leaked patch here would silently attribute a LATER run's
    connections to this witness, and both runs would look wrong for reasons
    neither could explain.

    `observed` is set on ENTRY, not on exit: a block that raises still observed
    whatever it managed to attempt, and marking it unobserved would discard the
    evidence from exactly the runs worth investigating -- the same argument that
    puts `llm._record(source)` before validation rather than after.
    """
    record = NetworkWitness()
    original = socket.socket.connect
    record.observed = True

    def watched(self: socket.socket, address: object) -> object:
        host = _host_of(address)
        if host:
            record.hosts.append(host)
            if _is_aws(host):
                record.aws_hosts.append(host)
                if strict:
                    raise ContactedAWS(
                        f"this run attempted a connection to the AWS host {host!r}; "
                        f"a self-hosted run must not. Raised at the call site "
                        f"responsible so the traceback names it."
                    )
        return original(self, address)

    socket.socket.connect = watched
    try:
        yield record
    finally:
        socket.socket.connect = original
