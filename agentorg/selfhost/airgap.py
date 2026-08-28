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

THE INTERCEPT IS AT TWO PLACES, AND THE SECOND ONE IS THE WHOLE MEASUREMENT.

`socket.socket.connect` is the obvious position and it is NOT SUFFICIENT.
MEASURED, and it is this lane's most important finding: a real Bedrock run --
`source=model`, `us.amazon.nova-2-lite-v1:0`, ten outbound connections -- was
reported by a connect-only witness as

    network: no AWS host contacted -- 10 outbound connection(s) to 8 distinct hosts
    is_airgapped_from_aws() -> True

because botocore resolves the hostname to an address BEFORE connecting, so every
one of those ten was recorded as a bare IP (`18.206.9.17`, …) matching no marker.
The check was FAIL-OPEN: it answered "air-gapped" for the one run that most
obviously was not, and nothing in the output looked wrong. That is precisely the
defect class this repository exists to prevent -- a check that cannot distinguish
"did not happen" from "passed" -- reproduced inside the check written to prove it.

So `socket.getaddrinfo` is intercepted too, and it is the one that sees NAMES:

    getaddrinfo hosts seen: ['bedrock-runtime.us-east-1.amazonaws.com']

Both are kept rather than only the resolver, because they answer different
questions and either alone is fail-open in a different direction. `getaddrinfo`
misses a connection to an IP literal (nothing resolves), and `connect` misses
every hostname (already resolved). The union is what makes the claim honest, and
`hosts` records both kinds -- so an IP in the evidence is not noise, it is the
part of the record whose ownership this module genuinely cannot determine.

Above these two -- in botocore, in strands, in `llm._complete` -- a witness
measures our own wrapper and would miss anything reaching the network another way.
Below them there is no Python. Every HTTP client in this dependency closure
(botocore's urllib3, httpx, requests, ollama's client) passes through both.

WHAT IT DOES NOT PROVE, stated because an over-claim here would be the same
defect one level up:

  - It observes THIS PROCESS. A subprocess -- `git clone`, a scanner binary --
    has its own socket module and is invisible here. `subprocess_note()` says so.
  - It observes CONNECTIONS AND RESOLUTIONS, not payloads. A connection to a
    non-AWS host that proxies to Bedrock would pass. Nothing on a laptop
    distinguishes those.
  - AN IP LITERAL IS UNATTRIBUTABLE. A caller that connects straight to an AWS
    address without resolving a name is recorded, and matched against no marker.
    `unresolved_ip_hosts()` reports how many such addresses were seen so a reader
    can judge the gap rather than be unaware of it.

So the honest form of the claim is: *no connection to any AWS-owned hostname was
resolved or attempted from this process during the run, and N bare IP addresses
were contacted whose ownership this witness cannot determine.* That is narrower
than "no AWS call" and it is what the evidence supports.
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
    #: Every name passed to `socket.getaddrinfo`. THE ONLY PLACE A HOSTNAME IS
    #: VISIBLE for a client that resolves before connecting, which botocore does --
    #: see the module docstring's measurement. Kept separately from `hosts` so a
    #: reader can tell a resolution from a connection.
    resolved_names: list[str] = field(default_factory=list)
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

    def unresolved_ip_hosts(self) -> list[str]:
        """Addresses contacted that were never resolved from a name here.

        THE HONEST GAP, REPORTED AS A NUMBER. A bare IP cannot be matched against
        a hostname marker, so these are the connections whose ownership this
        witness genuinely cannot determine. Under a working `getaddrinfo`
        intercept most connections have a name behind them and this list is
        small; a LARGE list means something is bypassing DNS and the air-gap
        claim is correspondingly weaker.

        Reported rather than suppressed because a caveat kept out of the artifact
        is one nobody quoting the number will see.
        """
        return [h for h in dict.fromkeys(self.hosts)
                if h not in self.resolved_names and _looks_like_ip(h)]

    def summary(self) -> str:
        """One line for a log or a slide. Names the unobserved case explicitly."""
        if not self.observed:
            return "network: NOT OBSERVED -- no witness was installed, so this " \
                   "run's outbound connections are unknown"
        if self.aws_hosts:
            unique = sorted(set(self.aws_hosts))
            return (f"network: REACHED AWS -- {len(self.aws_hosts)} contact(s) "
                    f"with {len(unique)} AWS host(s): {', '.join(unique)}")
        unattributable = self.unresolved_ip_hosts()
        tail = ""
        if unattributable:
            # NAMED IN THE SAME SENTENCE AS THE REASSURING HALF. A reader who
            # stops after "no AWS host" must still have seen the gap; putting it
            # in a second line the caller may not print is how a caveat gets lost.
            tail = (f"; {len(unattributable)} bare IP address(es) contacted whose "
                    f"ownership this witness cannot determine")
        return (f"network: no AWS hostname resolved or contacted -- "
                f"{len(self.hosts)} connection(s), "
                f"{len(set(self.resolved_names))} name(s) resolved{tail}")

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


def _looks_like_ip(host: str) -> bool:
    """True when `host` is an address literal rather than a name.

    Deliberately crude and deliberately INCLUSIVE of IPv6: anything with no
    alphabetic character beyond hex digits and separators is treated as an
    address. It feeds `unresolved_ip_hosts`, whose job is to over-report the gap
    rather than under-report it -- a name misfiled as an IP inflates a caveat,
    while an IP misfiled as a name would quietly shrink one.
    """
    stripped = host.strip("[]")
    if not stripped:
        return False
    return all(c in "0123456789abcdefABCDEF.:%" for c in stripped)


@contextmanager
def witness(*, strict: bool = False) -> Iterator[NetworkWitness]:
    """Record every name resolved and every address connected to by this process.

    Patches `socket.getaddrinfo` AND `socket.socket.connect` for the duration and
    restores both in a `finally`, so an exception inside the block cannot leave
    either interceptor installed. A leaked patch would silently attribute a LATER
    run's traffic to this witness, and both runs would look wrong for reasons
    neither could explain.

    BOTH SEAMS ARE REQUIRED. A connect-only witness reported a real Bedrock run as
    air-gapped -- see the module docstring. The resolver is where hostnames are
    visible; connect is where IP literals are. Either alone fails open.

    `observed` is set on ENTRY, not on exit: a block that raises still observed
    whatever it managed to attempt, and marking it unobserved would discard the
    evidence from exactly the runs worth investigating -- the same argument that
    puts `llm._record(source)` before validation rather than after.
    """
    record = NetworkWitness()
    original_connect = socket.socket.connect
    original_getaddrinfo = socket.getaddrinfo
    record.observed = True

    def note(host: str) -> None:
        """Record one host and refuse it under `strict`. One writer for one event."""
        if not host:
            return
        if _is_aws(host):
            record.aws_hosts.append(host)
            if strict:
                raise ContactedAWS(
                    f"this run reached the AWS host {host!r}; a self-hosted run "
                    f"must not. Raised at the call site responsible so the "
                    f"traceback names it."
                )

    def watched_getaddrinfo(host: object, port: object, *args: object,
                            **kwargs: object) -> object:
        if isinstance(host, str) and host:
            record.resolved_names.append(host)
            note(host)
        return original_getaddrinfo(host, port, *args, **kwargs)

    def watched_connect(self: socket.socket, address: object) -> object:
        host = _host_of(address)
        if host:
            record.hosts.append(host)
            note(host)
        return original_connect(self, address)

    socket.getaddrinfo = watched_getaddrinfo
    socket.socket.connect = watched_connect
    try:
        yield record
    finally:
        socket.socket.connect = original_connect
        socket.getaddrinfo = original_getaddrinfo
