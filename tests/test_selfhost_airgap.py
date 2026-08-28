"""The network witness: what it proves, and what it must refuse to claim.

THE DEFECT THESE TESTS EXIST FOR IS REAL AND WAS SHIPPED. The first version of
`airgap.py` patched only `socket.socket.connect`, and a genuine Bedrock run --
`source=model`, ten calls to `bedrock-runtime.us-east-1.amazonaws.com` -- was
reported as `is_airgapped_from_aws() -> True`, because botocore resolves the name
before connecting so every contact was recorded as a bare IP. The check was
fail-open in the one direction that matters.

So the tests below are written against the CONSEQUENCE rather than the mechanism.
`test_a_resolved_hostname_is_recorded_even_when_the_connection_uses_an_address`
reproduces the exact shape of the defect: resolve a name, connect to an address,
and assert the witness saw AWS. A test asserting "getaddrinfo is patched" would
pass against a patch that recorded nothing.
"""

from __future__ import annotations

import socket

import pytest

from agentorg.selfhost import airgap


def test_the_marker_list_is_not_empty_or_every_run_reads_as_airgapped():
    """A matcher over an empty list finds nothing and calls every run clean.

    The vacuity guard this repository asks for everywhere: a check that cannot
    fail reads as coverage. If `AWS_HOST_MARKERS` were ever emptied, every test
    below would still pass while the witness reported air-gap for a Bedrock run.
    """
    assert airgap.AWS_HOST_MARKERS, (
        "AWS_HOST_MARKERS is empty; every host would match no marker and the "
        "witness would report every run as air-gapped"
    )
    # The three suffixes are asserted individually rather than as a set equality,
    # so ADDING a marker is not a test failure while REMOVING one is.
    assert "amazonaws.com" in airgap.AWS_HOST_MARKERS
    assert "amazonaws.com.cn" in airgap.AWS_HOST_MARKERS, (
        "the China partition suffix is missing; a run reaching it would be "
        "recorded as clean"
    )
    assert "api.aws" in airgap.AWS_HOST_MARKERS, (
        "the dual-stack suffix is missing; a run reaching it would be clean"
    )


def test_an_uninstalled_witness_reports_UNOBSERVED_and_never_airgapped():
    """Doing nothing must not produce the most reassuring answer.

    `hosts` and `aws_hosts` are empty both for a clean run and for a witness
    nobody installed. Reading air-gap off emptiness would make "we did not check"
    indistinguishable from "we checked and it was clean" -- the distinction
    `scan_provenance` exists for, one layer over.
    """
    never_ran = airgap.NetworkWitness()
    assert never_ran.observed is False
    assert never_ran.is_airgapped_from_aws() is False, (
        "a witness that was never installed claimed air-gap"
    )
    assert "NOT OBSERVED" in never_ran.summary()


def test_a_local_only_run_is_airgapped_and_says_how_many_hosts():
    """The positive case, and it must be reachable or the suite proves nothing."""
    with airgap.witness() as record:
        # A resolution and a connection to a loopback name, no network required.
        socket.getaddrinfo("localhost", 80, proto=socket.IPPROTO_TCP)
    assert record.observed is True
    assert record.is_airgapped_from_aws() is True
    assert "localhost" in record.resolved_names
    assert not record.aws_hosts


def test_a_resolved_hostname_is_recorded_even_when_the_connection_uses_an_address():
    """THE SHIPPED DEFECT, as a test. botocore's exact shape.

    Resolve an AWS name, then connect to a bare address -- which is what every
    botocore call does. A connect-only witness records only the address, matches
    it against no marker, and reports air-gap. This asserts the CONSEQUENCE (the
    witness knows it reached AWS) rather than the mechanism (getaddrinfo is
    patched), because a patch that recorded nothing would satisfy the latter.

    No network is touched: `getaddrinfo` is called through the witness with a
    name that resolves locally being irrelevant -- what matters is that the
    witness saw the NAME. The connect is to a closed loopback port and its
    failure is expected.
    """
    with airgap.witness() as record:
        try:
            socket.getaddrinfo("bedrock-runtime.us-east-1.amazonaws.com", 443,
                               proto=socket.IPPROTO_TCP)
        except OSError:
            # DNS may be unavailable in a hermetic environment. The witness
            # records the name BEFORE delegating, so the recording still happened
            # -- which is exactly why the assertion below is outside this try.
            pass
        sock = socket.socket()
        sock.settimeout(0.05)
        try:
            sock.connect(("127.0.0.1", 9))
        except OSError:
            pass
        finally:
            sock.close()

    assert record.aws_hosts, (
        "the witness did not record an AWS host for a resolved AWS hostname; "
        "this is the fail-open defect that reported a real Bedrock run as "
        "air-gapped"
    )
    assert record.is_airgapped_from_aws() is False
    assert "REACHED AWS" in record.summary()


def test_strict_raises_at_the_call_site_that_reached_aws():
    """`strict` must stop the run, not tally it for a report nobody reads."""
    with pytest.raises(airgap.ContactedAWS) as caught, airgap.witness(strict=True):
        socket.getaddrinfo("sts.amazonaws.com", 443, proto=socket.IPPROTO_TCP)
    assert "sts.amazonaws.com" in str(caught.value)


def test_both_seams_are_restored_even_when_the_block_raises():
    """A leaked patch would attribute a LATER run's traffic to this witness.

    BOTH names are captured before the block, because restoring one and leaking
    the other is a real possible bug: they are two separate assignments in
    `witness`'s `finally`, and a witness that restored only `connect` would leave
    every later test's DNS running through a dead closure.
    """
    before_connect = socket.socket.connect
    before_getaddrinfo = socket.getaddrinfo
    with pytest.raises(RuntimeError), airgap.witness():
        raise RuntimeError("the block failed")
    assert socket.socket.connect is before_connect, "connect was left patched"
    assert socket.getaddrinfo is before_getaddrinfo, "getaddrinfo was left patched"


def test_a_run_that_raised_still_counts_as_OBSERVED():
    """The evidence from a failed run is the evidence most worth keeping.

    Same argument as `agent_client` recording provenance BEFORE validation: a run
    that reached AWS and then died did still reach AWS, and marking it unobserved
    would discard exactly the record somebody needs.
    """
    captured: list[airgap.NetworkWitness] = []
    with pytest.raises(RuntimeError), airgap.witness() as record:
        captured.append(record)
        socket.getaddrinfo("localhost", 80, proto=socket.IPPROTO_TCP)
        raise RuntimeError("died mid-run")
    assert captured, "the witness never yielded; this test would pin nothing"
    assert captured[0].observed is True
    assert "localhost" in captured[0].resolved_names


def test_a_unix_socket_connect_is_not_recorded_as_a_network_host():
    """AF_UNIX addresses are strings, and the docker daemon socket is one.

    Recording it would put a non-network path in evidence about the network, and
    the count of "hosts contacted" is a number somebody will quote.
    """
    assert airgap._host_of("/var/run/docker.sock") == ""
    assert airgap._host_of(("10.0.0.1", 443)) == "10.0.0.1"
    # IPv6 tuples carry four elements and must still yield their host.
    assert airgap._host_of(("::1", 443, 0, 0)) == "::1"


def test_the_unattributable_ip_gap_is_reported_rather_than_hidden():
    """A bare IP cannot be matched against a hostname marker. Say so.

    The caveat is asserted on the RENDERED summary, not on the list, because a
    caveat that exists only as a method nobody calls is one no reader sees --
    the same reason `report._pct` is what the cost lane's alarm asserts over.
    """
    record = airgap.NetworkWitness(observed=True, hosts=["203.0.113.7"])
    assert record.unresolved_ip_hosts() == ["203.0.113.7"]
    summary = record.summary()
    assert "bare IP address(es)" in summary, (
        "the summary did not name the unattributable-IP gap, so a reader "
        "quoting 'no AWS hostname contacted' would not know its limit"
    )
    # A resolved name is NOT an unattributable IP, even though both are hosts.
    named = airgap.NetworkWitness(observed=True, hosts=["example.com"],
                                  resolved_names=["example.com"])
    assert named.unresolved_ip_hosts() == []


def test_the_subprocess_limit_is_stated_in_the_artifact():
    """A scanner binary has its own socket module and is invisible here."""
    note = airgap.NetworkWitness().subprocess_note()
    assert "this process only" in note
