"""Unit tests for the target login app.

Run from the target_repo/ directory:

    python -m pytest tests -q
"""

import pytest

from app.auth import authenticate, create_app


def test_authenticate_accepts_known_user():
    assert authenticate("alice", "wonderland") is True


def test_authenticate_rejects_bad_password():
    assert authenticate("alice", "nope") is False


def test_authenticate_requires_both_fields():
    assert authenticate("", "wonderland") is False
    assert authenticate("alice", "") is False


@pytest.fixture()
def client():
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_login_ok_with_valid_credentials(client):
    response = client.post(
        "/login",
        data={
            "username": "alice",
            "password": "wonderland",
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_login_rejects_invalid_credentials(client):
    response = client.post(
        "/login",
        data={
            "username": "alice",
            "password": "wrong",
        },
    )

    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid credentials"}