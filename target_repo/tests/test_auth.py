"""Tiny test so CI has something green to run. Owner: Reem."""

from app.auth import authenticate


def test_authenticate_requires_both_fields():
    assert authenticate("alice", "pw") is True
    assert authenticate("", "pw") is False
    assert authenticate("alice", "") is False
