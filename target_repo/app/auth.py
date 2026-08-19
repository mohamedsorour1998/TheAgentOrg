"""Minimal Flask login handler — the file the agents modify.

Owner: Reem. Keep this tiny; it only needs to be a realistic target for a diff.
A "add a per-IP login rate limit" ticket touches authenticate() and login().
"""

from flask import Flask, request, jsonify


# Toy in-memory user table.
# This is only for testing; real authentication is out of scope.
_USERS = {
    "alice": "wonderland",
    "bob": "builder",
}


def authenticate(username: str, password: str) -> bool:
    """Return True only when username/password match a known user."""
    if not username or not password:
        return False

    return _USERS.get(username) == password


def login():
    """POST /login — 200 on valid credentials, 401 otherwise."""

    username = request.form.get("username", "")
    password = request.form.get("password", "")

    if not authenticate(username, password):
        return jsonify({"error": "invalid credentials"}), 401

    return jsonify({"ok": True}), 200


def create_app() -> Flask:
    """App factory so tests can spin up an isolated client."""

    app = Flask(__name__)

    app.add_url_rule(
        "/login",
        view_func=login,
        methods=["POST"],
    )

    return app