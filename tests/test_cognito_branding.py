"""The sign-in page's appearance, and the four ways it silently stops being right.

**THE SIGN-IN PAGE IS A DIFFERENT ORIGIN SERVING A PAGE THIS REPOSITORY DOES NOT
RENDER.** Nothing in `web/`'s four gates can see it: `tsc`, `eslint`, `vitest` and
`next build` all read the application, and the login page is drawn by Cognito from a
JSON document. So the palette is written twice by necessity, and two copies keep
agreeing right up until one moves.

Every check here is one the reference deployment (`~/sorour/AgentsforHumansHackathon`)
paid for against a live pool, and three of the four are about a call that returns
**200 while changing nothing visible**.
"""

from __future__ import annotations

import re
from pathlib import Path

from infra.cognito import spec

REPO_ROOT = Path(__file__).resolve().parents[1]
GLOBALS_CSS = REPO_ROOT / "web" / "app" / "globals.css"


def _declared_colours() -> set[str]:
    """Every `#rrggbb` in the application's stylesheet, lower-cased."""
    return {m.lower() for m in re.findall(r"#[0-9a-fA-F]{6}\b", GLOBALS_CSS.read_text())}


def test_the_login_page_uses_the_application_s_palette() -> None:
    """The one check that stops a redesign leaving sign-in in last month's colours.

    The reference deployment records this exact test being *watched failing* on a
    changed hex value, and says why it matters: "without it, a colour changed in one
    place and not the other is invisible until someone opens both pages."
    """
    declared = _declared_colours()

    # ANTI-VACUITY. An empty set makes the loop below assert nothing at all, which
    # is the failure this repository names most often -- an empty result that reads
    # as a clean answer.
    assert len(declared) > 5, (
        f"only {len(declared)} colours parsed out of {GLOBALS_CSS}; this test would pin nothing"
    )

    for name, colour in spec.PALETTE.items():
        assert colour.lower() in declared, (
            f"spec.PALETTE[{name!r}] is {colour}, which appears nowhere in "
            f"web/app/globals.css. The sign-in page and the dashboard have drifted."
        )


def test_the_background_and_logo_are_switched_ON() -> None:
    """`enabled` defaults to **False** for both, and the API returns 200 either way.

    This is the single distinction between a branded page and one that reads as
    unstyled. The reference deployment shipped colours alone and got a white card on
    near-white with a heading Cognito wrote -- a green call and no change.
    """
    components = spec.branding_settings()["components"]
    assert components["pageBackground"]["image"]["enabled"] is True
    assert components["form"]["logo"]["enabled"] is True


def test_an_uploaded_asset_is_in_a_colour_mode_the_page_actually_ENTERS() -> None:
    """An asset stored under a mode the page never enters is never drawn.

    Same silent shape as a switch left off: the upload succeeds, the asset is
    stored, `describe` lists it, and no one ever sees it.
    """
    mode = spec.branding_settings()["categories"]["global"]["colorSchemeMode"]
    assert mode == "DARK", "the application has one palette and it is dark"

    modes = {asset["ColorMode"] for asset in spec.branding_assets()}
    assert modes == {mode}, (
        f"assets are uploaded for {sorted(modes)} while the page renders in {mode}; "
        "anything outside that set is stored and never drawn"
    )


def test_the_form_logo_is_within_the_aspect_ratio_the_api_enforces() -> None:
    """Between 1:1 and 4:1, or `Invalid file dimension`.

    Measured by the reference deployment off a rejection rather than read in the
    docs: a 360x54 lockup was refused and 360x96 accepted. A test rather than a
    comment because the failure arrives at provision time, in front of whoever is
    deploying, and reads as a broken script.
    """
    svg = spec.login_logo_svg()
    width = int(re.search(r'width="(\d+)"', svg).group(1))
    height = int(re.search(r'height="(\d+)"', svg).group(1))
    ratio = width / height

    assert 1.0 <= ratio <= 4.0, (
        f"the form logo is {width}x{height} ({ratio:.2f}:1); Cognito refuses "
        "anything outside 1:1..4:1 with `Invalid file dimension`"
    )


def test_no_svg_carries_aria_on_its_root_element() -> None:
    """Cognito's SVG sanitiser REFUSES it: `element [svg#role] is not allowed`.

    Recorded as a constraint rather than an accessibility oversight. The background
    is decorative, and the logo's text IS the form's heading, so nothing here is
    carrying meaning that ARIA would have to restate.
    """
    for name, svg in (
        ("background", spec.login_background_svg()),
        ("logo", spec.login_logo_svg()),
        ("favicon", spec.login_favicon_svg()),
    ):
        root = svg[: svg.index(">") + 1]
        assert "role=" not in root, f"{name}'s root <svg> carries role=, which the sanitiser refuses"
        assert "aria-" not in root, f"{name}'s root <svg> carries aria-, which the sanitiser refuses"


def test_every_branding_colour_is_eight_hex_digits() -> None:
    """`rrggbbaa`, no `#`. A six-digit value is accepted and drawn as nothing."""
    settings = spec.branding_settings()

    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str) and ("olor" in key):
                    found.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(settings)

    # ANTI-VACUITY: a walker that found nothing would assert nothing.
    assert len(found) >= 6, f"only {len(found)} colours found in the settings document"

    for value in found:
        # `colorSchemeMode: "DARK"` is a mode, not a colour -- skip the enum.
        if value.isalpha():
            continue
        assert re.fullmatch(r"[0-9a-f]{8}", value), (
            f"{value!r} is not `rrggbbaa`; six digits is accepted by the API and drawn as nothing"
        )
