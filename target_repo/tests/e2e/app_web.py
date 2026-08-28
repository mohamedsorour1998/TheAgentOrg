"""G3 — the browser surface, wired WITHOUT editing app/auth.py.

`app/auth.py` is the file every clean and poisoned diff in this repository is written
against, and `fixtures/dev_result_poisoned.json` carries the reference diff
`scripts/preflight.py` loads to prove the deployed scanners are real. CLAUDE.md is
explicit that `REAL_SCANNER_LINES` is a property of the scanners AND of that exact
diff -- a single missing blank line moved the reported findings from {3,4} to {2,3}.

So this module WRAPS `create_app()` rather than changing it: it imports the factory,
adds the two template routes, and leaves `authenticate` and `login` untouched. The
poisoned diff still applies, the discriminator still reads {3, 4}, and the browser
surface exists.

The four-line alternative -- adding these routes to `create_app` directly -- is the
right long-term shape and belongs to that file's owner. It is reported rather than
taken.

RUN IT:
    cd target_repo && python -m pytest tests/e2e -q

`python -m pytest`, never the bare console script. Measured in this repository: the
bare form dies with `ModuleNotFoundError: No module named 'app'` during collection,
because `python -m` prepends cwd to `sys.path` and the console script does not.
"""

from __future__ import annotations

import socket
import threading
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

from flask import render_template, request

from app.auth import authenticate, create_app

TEMPLATES = Path(__file__).resolve().parent / "templates"


def create_web_app():
    """`create_app()` plus a GET form and a POST that renders its own result.

    The POST route is `/web/login`, NOT `/login`. Overriding `/login` would change
    the behaviour of the endpoint the unit tests and every generated API test drive,
    and a browser test that only passes because it rewrote the thing under test is
    worth less than no browser test at all.
    """
    app = create_app()
    app.template_folder = str(TEMPLATES)
    app.jinja_loader.searchpath = [str(TEMPLATES)]

    def form():
        return render_template("login.html", message=None, status="")

    def submit():
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if authenticate(username, password):
            return render_template("login.html", message="ok", status="200"), 200
        return render_template("login.html", message="invalid credentials", status="401"), 401

    app.add_url_rule("/web/login", view_func=form, methods=["GET"])
    app.add_url_rule("/web/login", view_func=submit, methods=["POST"], endpoint="web_submit")
    return app


class _QuietHandler(WSGIRequestHandler):
    """A request handler that does not print a line per request.

    Under `pytest -s` the default handler interleaves its access log with the test
    output, which makes a real failure hard to find on a projector.
    """

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass


class LiveServer:
    """A real HTTP server on a real port, for a real browser to drive.

    NOT `app.test_client()`. The test client is an in-process WSGI shim -- it never
    opens a socket, so a browser cannot reach it, and a test that used it would be an
    API test wearing a browser test's name. That substitution is exactly the shape
    CLAUDE.md warns about: a double that cannot express the failing case.

    Port 0 lets the OS choose, because a hardcoded port makes two concurrent runs
    fight and the loser reports a connection error that reads like a broken app.
    """

    def __init__(self, app) -> None:
        self._server = make_server("127.0.0.1", 0, app, handler_class=_QuietHandler)
        self.port = self._server.server_port
        self.url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> LiveServer:
        self._thread.start()
        self._wait_until_listening()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def _wait_until_listening(self, timeout: float = 5.0) -> None:
        """Connect once before yielding, so a browser never races the server's start.

        Without this the first `driver.get` can arrive before `serve_forever` is in
        its accept loop, and the failure is an intermittent connection refused -- a
        flake, in the one feature whose whole job is not to be flaky.
        """
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.25):
                    return
            except OSError:
                time.sleep(0.02)
        raise RuntimeError(f"the live server never accepted a connection on {self.port}")
