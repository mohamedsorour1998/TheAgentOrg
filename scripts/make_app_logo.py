"""Draw the GitHub App's logo, and check the saved file rather than the plan.

**GENERATED, NOT HAND-BUILT**, for `make_deck.py`'s reason: a hand-made PNG lets a
colour drift out of the palette with nothing to notice, and nobody can fix it at
11pm without the original file. Every value below is a constant traced to
`web/app/globals.css`, and `verify()` re-opens the saved PNG and reads the pixels
back -- a logo that silently saved wrong is byte-different and looks fine in a
directory listing.

## The mark IS the product's thesis, not a decoration of it

A pipeline runs, and it **stops at a gate a person must open**. That is the whole
argument of this repository, and `StageSpine` already draws it: an agent stage is a
filled dot, a gate is a HOLLOW RING, because a decision is a different kind of thing
from a step that merely runs.

So the icon is a line that stops, a ring, and the line resuming. Nothing else. At 20
pixels in a pull-request comment the ring is still a ring, which is the only thing
that has to survive.

**REJECTED, and recorded so it is not re-proposed:**

  * The wordmark. "The Agent Org." is four words and a full stop; at 20px it is a
    grey smear. A logo is not a signature.
  * The nine-stage spine used on the Cognito page. Nine marks in a 512px square are
    six pixels each -- it reads as texture, and texture is what a background is for.
  * A robot, a shield, a padlock. Three products in five have one, none of them says
    what this does, and a padlock claims a guarantee this makes deliberately narrow.

## Two constraints measured rather than assumed

  * **FULL-BLEED BACKGROUND, no rounded corners drawn here.** GitHub masks the logo
    itself -- a circle beside a comment, a rounded square in the install flow -- and
    a corner radius baked into the file shows as a dark notch inside GitHub's own
    mask. Let the platform do the cropping it is going to do anyway.
  * **DRAWN AT 4x AND DOWNSAMPLED.** Pillow's `ellipse` has no antialiasing, so a
    ring drawn at final size has stepped edges that are obvious at any size above a
    favicon. Supersampling is what buys the smooth curve.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "docs" / "brand" / "github-app-logo.png"

# Traced to `web/app/globals.css`. Restated here because that file is CSS and this
# is Pillow; `tests/test_app_logo.py` reads both and asserts they agree, which is
# the same guard `infra/cognito/spec.py`'s palette carries.
SURFACE = (11, 15, 23)  # #0b0f17 -- near-black with a hint of blue
ACCENT = (34, 211, 238)  # #22d3ee -- cyan: the one saturated colour
MUTED = (139, 151, 171)  # #8b97ab -- the line the pipeline runs along

SIZE = 512
SCALE = 4  # supersample; see the module docstring

# THE GEOMETRY, as fractions of the canvas so the numbers survive a size change.
RING_RADIUS = 0.215  # outer radius of the gate
RING_STROKE = 0.062  # thick enough to read at 20px
LINE_WIDTH = 0.034
LINE_INSET = 0.135  # where the pipeline enters and leaves the frame
GAP = 0.052  # clear space between the line and the ring


def render(size: int = SIZE) -> Image.Image:
    """The mark: a line that stops at a gate, and resumes past it."""
    big = size * SCALE
    image = Image.new("RGB", (big, big), SURFACE)
    draw = ImageDraw.Draw(image)

    mid = big / 2
    radius = big * RING_RADIUS
    stroke = max(1, round(big * RING_STROKE))
    line_w = max(1, round(big * LINE_WIDTH))
    inset = big * LINE_INSET
    gap = big * GAP

    # THE LINE, in two segments. The gap is the point: the pipeline does not pass
    # through the gate on its own.
    draw.line([(inset, mid), (mid - radius - gap, mid)], fill=MUTED, width=line_w)
    draw.line([(mid + radius + gap, mid), (big - inset, mid)], fill=MUTED, width=line_w)

    # THE GATE. Hollow, exactly as `StageSpine` draws a decision a person makes --
    # a filled dot would say "a stage that ran", which is the opposite meaning.
    draw.ellipse(
        [(mid - radius, mid - radius), (mid + radius, mid + radius)],
        outline=ACCENT,
        width=stroke,
    )

    return image.resize((size, size), Image.LANCZOS)


def verify(path: Path) -> list[str]:
    """Re-open the SAVED file and read its pixels. Returns problems, empty if fine.

    Checking the image object we just built would only prove the plan; this proves
    the artifact. Same reason `make_deck.py` re-opens the `.pptx` archive.
    """
    problems: list[str] = []
    image = Image.open(path).convert("RGB")

    if image.size != (SIZE, SIZE):
        problems.append(f"not square at the intended size: {image.size}")
    if min(image.size) < 200:
        problems.append("GitHub wants at least 200x200")

    mid = SIZE // 2

    # The corner is the background, so the mask GitHub applies has something to cut.
    if image.getpixel((4, 4)) != SURFACE:
        problems.append(f"the corner is {image.getpixel((4, 4))}, not the surface colour")

    # THE RING IS ACTUALLY THERE. Walking the middle row outwards from the centre
    # must cross cyan -- an ellipse drawn with a zero width, or in the background
    # colour, saves perfectly and shows nothing.
    row = [image.getpixel((x, mid)) for x in range(SIZE)]
    if not any(_is_accent(p) for p in row):
        problems.append("no accent pixel on the centre row: the ring did not draw")

    # THE CENTRE IS HOLLOW. A filled circle would read as "a stage that ran", which
    # is the opposite of what a gate means in this product.
    if _is_accent(image.getpixel((mid, mid))):
        problems.append("the centre is filled: that is an agent stage, not a gate")

    # THE LINE STOPS. Between the ring and the centre there must be background, or
    # the line runs through the gate and the mark says the pipeline ignores it.
    if not any(p == SURFACE for p in row[mid : mid + int(SIZE * RING_RADIUS)]):
        problems.append("no clear space inside the ring: the line runs through the gate")

    return problems


def _is_accent(pixel: tuple[int, int, int]) -> bool:
    """Close to the accent, allowing for the downsample's blending at the edges."""
    return all(abs(a - b) < 60 for a, b in zip(pixel, ACCENT, strict=True))


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    render().save(OUT, "PNG", optimize=True)

    problems = verify(OUT)
    size_kb = OUT.stat().st_size / 1024
    print(f"{OUT.relative_to(REPO_ROOT)}  ({size_kb:.1f} KB, {SIZE}x{SIZE})")
    for problem in problems:
        print(f"  FAIL: {problem}")
    if problems:
        return 1

    print("  ring: hollow · line: stops at the gate · corner: surface")
    print("  OK — read back from the saved file, not from the drawing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
