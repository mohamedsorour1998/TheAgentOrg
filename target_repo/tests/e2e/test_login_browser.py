"""G4 — Selenium against a live instance. Real browser or an HONEST skip.

READ THIS FIRST: NO BROWSER RAN ON THE MACHINE THAT WROTE THIS FILE. Measured
2026-08-28 in `.venv-main`:

    pip list | grep -i selenium            -> (nothing)
    which chromedriver geckodriver         -> chromedriver not found
                                              geckodriver not found
    ls /Applications/Google\\ Chrome.app    -> No such file or directory
    ls /Applications/Firefox.app           -> No such file or directory
    safaridriver -p 4444                   -> hung; no /status response in 120s
                                              (needs Safari > Develop > Allow
                                               Remote Automation, a GUI action)

So these tests SKIP here, and the skip is the honest answer. What is verified
locally is everything up to the browser: the live server serves the form over a
real socket, and the assertions below are the ones a driver would make. What is
NOT verified is that Selenium drives them, and that gap is stated rather than
papered over -- presenting a stubbed driver as a Selenium run would be exactly the
defect this repository exists to prevent.

### WHY THE SKIP IS SAFE HERE AND WOULD NOT BE IN THE PIPELINE

CLAUDE.md: "a check that cannot distinguish 'did not run' from 'passed' is the
defect this whole project exists to prevent." A skip is precisely that shape, so
two things make it acceptable:

  1. `SELENIUM_REQUIRED=true` promotes the skip to a FAILURE. Same knob shape as
     `SCANNERS_REQUIRED`, which promotes an absent scanner from a dev affordance
     to a fault. CI sets it; a laptop does not.
  2. `test_the_skip_is_visible_and_not_silent` RUNS UNCONDITIONALLY and reports
     what is missing. A suite that quietly collects zero browser tests reads
     identically to one where they all passed.

Without both, this file is theatre -- G6's argument applied to itself.

### WHY selenium IS NOT A DEPENDENCY OF agentorg/

`tests/test_agentcore_deploy_assets.py::test_requirements_covers_every_third_party_import_in_the_package`
AST-walks `agentorg/` and requires every third-party import to appear in
`agents/requirements.txt` -- so a `selenium` import under `agentorg/` becomes a
dependency of all five arm64 agent containers. That test already refuses
`starlette` for this reason. This file is `target_repo/tests/e2e/`, outside
`agentorg/`, and the import is INSIDE the fixture so collection never needs it.

RUN IT:
    cd target_repo && python -m pytest tests/e2e -q

`python -m pytest`, never bare `pytest`: measured, the bare form dies with
`ModuleNotFoundError: No module named 'app'` during collection.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app_web import LiveServer, create_web_app  # noqa: E402 - after the sys.path line above

# The knob, read through `os.environ` at CALL time and never bound at import.
# CLAUDE.md: `from ..common.config import SCANNERS_REQUIRED` binds the value before
# any fixture runs, so the knob would silently ignore both the tests and the
# deployed environment.
REQUIRED_ENV = "SELENIUM_REQUIRED"


def _required() -> bool:
    """True when a missing browser must FAIL rather than skip.

    `== "true"` case-insensitively, never `bool(os.environ.get(...))` -- the latter
    reads the string "false" as True, which is the trap every knob in
    `agentorg/common/config.py` is written to avoid.
    """
    return os.environ.get(REQUIRED_ENV, "").strip().lower() == "true"


def _what_is_missing() -> list[str]:
    """Every reason a browser cannot be driven here, named. Empty means it can.

    A LIST rather than a bool, because "selenium is not installed" and "selenium is
    installed and no driver exists" are different problems with different fixes, and a
    single False sends the reader to look for the wrong one.
    """
    missing: list[str] = []
    try:
        import selenium  # noqa: F401 - probing availability
    except ImportError:
        missing.append("the `selenium` package is not installed")

    drivers = [name for name in ("chromedriver", "geckodriver") if shutil.which(name)]
    if not drivers:
        missing.append("no chromedriver or geckodriver on PATH")
    return missing


@pytest.fixture()
def driver():
    """A headless browser, or a skip naming exactly what is absent.

    Headless is not optional in CI, and `--no-sandbox` / `--disable-dev-shm-usage` are
    the two flags a containerised Chrome needs: without the second it dies on a shared
    memory segment far smaller than a page render wants, and the error names /dev/shm
    rather than the browser.
    """
    missing = _what_is_missing()
    if missing:
        message = "no browser available: " + "; ".join(missing)
        if _required():
            pytest.fail(f"{REQUIRED_ENV}=true and {message}")
        pytest.skip(message)

    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    browser = webdriver.Chrome(options=options)
    browser.set_page_load_timeout(20)
    try:
        yield browser
    finally:
        browser.quit()


@pytest.fixture()
def server():
    """A real instance on a real port. See app_web.LiveServer for why not test_client."""
    with LiveServer(create_web_app()) as live:
        yield live


# ── the browser tests ──────────────────────────────────────────────────────────

def test_the_login_form_renders_in_a_real_browser(driver, server):
    """The page loads and the three controls exist. Located by id, never by position."""
    from selenium.webdriver.common.by import By

    driver.get(f"{server.url}/web/login")

    assert driver.find_element(By.ID, "login-form")
    assert driver.find_element(By.ID, "username")
    assert driver.find_element(By.ID, "password")
    assert driver.find_element(By.ID, "submit")


def test_valid_credentials_signed_in_through_the_browser(driver, server):
    """The happy path, typed into a real form and submitted by a real click."""
    from selenium.webdriver.common.by import By

    driver.get(f"{server.url}/web/login")
    driver.find_element(By.ID, "username").send_keys("alice")
    driver.find_element(By.ID, "password").send_keys("wonderland")
    driver.find_element(By.ID, "submit").click()

    result = driver.find_element(By.ID, "result")
    assert result.get_attribute("data-status") == "200", (
        f"the browser was told {result.get_attribute('data-status')} for valid "
        f"credentials; the page said {result.text!r}"
    )


def test_invalid_credentials_are_refused_through_the_browser(driver, server):
    """The refusal, and the STATUS as well as the words.

    Asserting only on the text would pass against a handler that returns 200 while
    saying "invalid credentials" -- a page that reads correctly and reports success,
    which is this project's signature defect shape.
    """
    from selenium.webdriver.common.by import By

    driver.get(f"{server.url}/web/login")
    driver.find_element(By.ID, "username").send_keys("alice")
    driver.find_element(By.ID, "password").send_keys("definitely-wrong")
    driver.find_element(By.ID, "submit").click()

    result = driver.find_element(By.ID, "result")
    assert result.get_attribute("data-status") == "401"
    assert "invalid credentials" in result.text.lower()


def test_an_empty_submission_is_refused_through_the_browser(driver, server):
    """Both fields blank. `authenticate` returns False for either empty, and must here."""
    from selenium.webdriver.common.by import By

    driver.get(f"{server.url}/web/login")
    driver.find_element(By.ID, "submit").click()

    assert driver.find_element(By.ID, "result").get_attribute("data-status") == "401"


# ── the skip is not silent ─────────────────────────────────────────────────────

def test_the_skip_is_visible_and_not_silent(capsys):
    """RUNS ALWAYS. Reports whether a browser drove the four tests above.

    This is the file's most important test on a machine with no browser, because
    without it `pytest -q` collects four skips and prints a number that looks like a
    clean run. G6 requires a quarantined test's absence to be reported; a skipped
    browser test is the same fact, and the same requirement applies to it.

    It does not FAIL when a browser is absent -- that is `SELENIUM_REQUIRED`'s job, and
    a test that failed on every laptop would be turned off within a day, which is the
    social failure mode this whole lane is written against.
    """
    missing = _what_is_missing()
    with capsys.disabled():
        if missing:
            print(
                f"\n  [G4] NO BROWSER RAN. The four Selenium tests in this file were "
                f"SKIPPED, not passed:\n"
                + "".join(f"    - {reason}\n" for reason in missing)
                + f"    Set {REQUIRED_ENV}=true to make this a failure instead.\n"
            )
        else:
            print("\n  [G4] a real browser is available; the Selenium tests ran.\n")

    assert isinstance(missing, list), "the probe returned no answer at all"
    if _required():
        assert not missing, (
            f"{REQUIRED_ENV}=true, so a missing browser is a FAULT: {missing}"
        )
