# A real browser ran the Selenium tests — and the premise was wrong

**Open item 5 said "Selenium has never run — no browser on this host". The second
half was false.** This document is the reproduction, and the correction is worth
more than the tests it unblocked.

---

## 1 · The absence that was not an absence

The 2026-08-28 probe ran these commands and every one of them answered correctly:

```
pip list | grep -i selenium          -> (nothing)
which chromedriver geckodriver       -> not found
ls /Applications/Google\ Chrome.app  -> No such file or directory
ls /Applications/Firefox.app         -> No such file or directory
safaridriver -p 4444                 -> hung; no /status in 120s
```

The conclusion drawn was *"no browser on this host"*. Measured 2026-09-09, same
machine:

```
/Users/sorour/.cache/puppeteer/chrome/mac_arm-152.0.7977.75/chrome-mac-arm64/
    Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing
$ "…/Google Chrome for Testing" --version
Google Chrome for Testing 152.0.7977.75
```

A real Chrome had been on the machine the whole time. It is not in `/Applications`
because **it is not an installed application** — it is a cached artifact some Node
tool downloaded for an unrelated reason.

Both commands were right about where they looked. `ls /Applications` is not
`find ~ -name`, and `which chromedriver` only reads `PATH` — which was also
misleading, because **selenium 4.6+ ships Selenium Manager and downloads the
matching driver itself**. Two true negatives, one false conclusion, and an entire
open item described as needing hardware that was already present.

> **An absence proved by looking in one place is not an absence.** Name the places
> you searched inside the claim, prefer searching by capability over expected path,
> and write "I did not find it in A, B or C" rather than "it is not installed". The
> first is measured; the second is an inference, and the inference is what reaches a
> report.

---

## 2 · The reproduction

Nothing here is installed into `.venv-main`, deliberately — see §4.

```bash
SCRATCH=$(mktemp -d)
.venv-main/bin/python -m pip install --target "$SCRATCH/sellib" selenium   # 4.48.0

# Selenium Manager resolves the matching driver. It does NOT need one on PATH;
# `_what_is_missing()` requires one anyway, conservatively — see §4.
SM="$SCRATCH/sellib/selenium/webdriver/common/macos/selenium-manager"
CHROME="$HOME/.cache/puppeteer/chrome/mac_arm-152.0.7977.75/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
"$SM" --browser chrome --browser-path "$CHROME" --driver chromedriver --output json
#   "driver_path": "~/.cache/selenium/chromedriver/mac-arm64/152.0.7977.82/chromedriver"

cd target_repo
export PATH="$HOME/.cache/selenium/chromedriver/mac-arm64/152.0.7977.82:$PATH"
export PYTHONPATH="$SCRATCH/sellib"
export SELENIUM_BROWSER_BINARY="$CHROME"
../.venv-main/bin/python -m pytest tests/e2e -q
```

```
....
  [G4] a real browser is available; the Selenium tests ran.

.                                                                    [100%]
5 passed in 133.08s (0:02:13)
```

`python -m pytest`, never the bare console script — that distinction is measured and
unchanged (§4).

---

## 3 · What the browser found, which nothing else could

**The first real run was `3 failed, 2 passed`,** and the failure is the reason this
was worth doing.

```
NoSuchElementException: {"method":"css selector","selector":"[id=\"result\"]"}
FAILED test_valid_credentials_signed_in_through_the_browser
FAILED test_invalid_credentials_are_refused_through_the_browser
FAILED test_an_empty_submission_is_refused_through_the_browser
```

`templates/login.html` carried `action="/login"` — `create_app()`'s **JSON API**,
which answers `{"error":"invalid credentials"}`. There is no `#result` element and
no `data-status` attribute in a JSON document, so the browser was reading JSON and
looking for HTML.

The consequence is larger than the three failures. `app_web.py` exists precisely so
the browser surface does **not** override the endpoint the unit tests and every
generated API test drive — CLAUDE.md states that in those words — and the form then
posted to that endpoint anyway. **The `/web/login` POST route the whole module exists
to add was reached by nothing.** That is this repository's second named pattern (*a
feature complete, tested, and reached by nothing*) arriving in an HTML attribute,
where ruff cannot read it, pytest never rendered it, and `render_template` is happy
either way.

The one test that passed is the one that never submits.

```
action="/login"      3 failed, 2 passed
action="/web/login"  5 passed
```

---

## 4 · What still skips, and why that is correct

**`selenium` is in no requirements file, so a clean checkout still skips all four.**
That is the right default and not an oversight:

| Direction | Result | Command |
|---|---|---|
| default | `1 passed, 4 skipped` + a named `[G4] NO BROWSER RAN` block | `pytest target_repo/tests/e2e -q` |
| `SELENIUM_REQUIRED=true` | `1 failed, 4 errors` — absent is a **FAULT** | same, with the knob |
| `SELENIUM_REQUIRED=false` | `1 passed, 4 skipped` — not the `bool(os.environ)` trap | same, with the knob |

All three re-measured from the repository root after the `testpaths` widening, and
all three are unchanged. A browser test that failed on every laptop is turned off
within a day; `SELENIUM_REQUIRED=true` is how CI demands otherwise.

**The driver-on-PATH probe is stricter than selenium needs**, and is kept. Selenium
Manager would resolve a driver on its own, but relaxing the probe turns "no browser"
from a *skip* into an *error* at `webdriver.Chrome()` — strictly worse.

**`SELENIUM_BROWSER_BINARY` is a knob, not a search.** Naming the path is checkable;
a search that guesses wrong reports "no browser" for a machine that has one, which is
the reading that kept this file unexecuted for twelve days.

**The two invocations stay apart.** `_load_app_web()` defers the `app_web` import and
its `sys.path` inserts to fixture time, because at module scope the insert leaks into
the session and pytest collects `tests/e2e/` before `tests/test_auth.py`. Re-measured
in `target_repo/` after every change here:

```
python -m pytest tests -q   ->  6 passed, 4 skipped
pytest tests -q             ->  ModuleNotFoundError: No module named 'app'
```

---

## 5 · The half that was the real defect

`pyproject.toml` said `testpaths = ["tests"]`, so **pytest never collected these
tests at all**. Lane G's skip machinery was correct in both directions and
unreachable — a test written to be honest about a missing browser was instead a test
nobody ran.

```
before   1969 collected      after   1974 collected      (+5)
ruff check agentorg scripts tests   All checks passed!   (unchanged — separate list)
```

`target_repo/tests/e2e` only, not `target_repo/tests`: `test_auth.py` imports `app`
at module scope, and putting `target_repo` on `sys.path` to satisfy it would
harmonise the two invocations above.

**Item 5's own metric could not express the fixed case.** `--collect-only | grep -ci
selenium` reads **0 before and 0 after** — no node id here contains the string
"selenium". The honest metric is the four test names, which is what
`tests/test_e2e_collection.py` asserts.
