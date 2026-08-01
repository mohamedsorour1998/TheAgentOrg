"""Minimal Flask login handler — the file the agents modify.

Owner: Reem. Keep this tiny; it only needs to be a realistic target for a diff.
"""

from flask import request, jsonify


def authenticate(username: str, password: str) -> bool:
    # Placeholder auth — replaced/extended by the developer agent's diff.
    return bool(username) and bool(password)


def login():
    user = authenticate(request.form["username"], request.form["password"])
    if not user:
        return jsonify({"error": "invalid credentials"}), 401
    return jsonify({"ok": True}), 200
