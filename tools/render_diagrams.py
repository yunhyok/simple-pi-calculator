"""Render the Help diagrams from SVG sources to PNG using PySide6 QtSvg.

Sources : docs/diagrams/*.svg (authored on a 900-unit wide canvas)
Targets : src/simple_pi_calculator/help/img/<name>.png     (760 px wide, shown 1:1 in the Help)
          src/simple_pi_calculator/help/img/<name>@2x.png  (2x resolution, picked automatically by
                                                          QTextBrowser on high-DPI screens)

QTextBrowser scales <img width=...> with nearest-neighbour sampling, which makes diagram text
jagged; therefore the PNG is rendered at exactly the display width and the high-DPI variant is
provided as a separate @2x file instead of being downscaled at display time.

Usage::

    python tools/render_diagrams.py                 # all diagrams
    python tools/render_diagrams.py --width 900     # other base width
    python tools/render_diagrams.py via_loop        # only selected diagrams (stem names)

Runs headless (QT_QPA_PLATFORM defaults to "offscreen"); needs no packages beyond PySide6.
The PNGs are committed so that builds do not need the Qt SVG image plugin.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SRC_DIR = REPO / "docs" / "diagrams"
OUT_DIR = REPO / "src" / "simple_pi_calculator" / "help" / "img"
DEFAULT_WIDTH = 760  # must match the width="760" attribute used in the Help pages


def render_svg(svg_path: Path, png_path: Path, width: int = DEFAULT_WIDTH) -> tuple[int, int]:
    """Render one SVG file to a PNG of the given pixel width (height keeps the aspect ratio)."""
    renderer = QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        raise ValueError(f"invalid SVG: {svg_path}")
    view = renderer.viewBoxF()
    if view.isEmpty():
        size = renderer.defaultSize()
        view = QRectF(0, 0, size.width(), size.height())
    height = max(1, round(width * view.height() / view.width()))
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
    renderer.render(painter, QRectF(0, 0, width, height))
    painter.end()
    png_path.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(png_path), "PNG"):
        raise OSError(f"could not write {png_path}")
    return width, height


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help="diagram stem names (default: all)")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH, help="output width in pixels")
    parser.add_argument("--src", type=Path, default=SRC_DIR, help="folder with SVG sources")
    parser.add_argument("--out", type=Path, default=OUT_DIR, help="output folder for PNG files")
    parser.add_argument("--no-hidpi", action="store_true", help="do not write the @2x variants")
    args = parser.parse_args(argv)

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])  # fonts need an application
    _ = app
    svgs = sorted(args.src.glob("*.svg"))
    if args.names:
        wanted = {n.removesuffix(".svg") for n in args.names}
        svgs = [p for p in svgs if p.stem in wanted]
        missing = wanted - {p.stem for p in svgs}
        if missing:
            print(f"not found: {', '.join(sorted(missing))}", file=sys.stderr)
            return 2
    if not svgs:
        print(f"no SVG files in {args.src}", file=sys.stderr)
        return 1
    for svg in svgs:
        targets = [(args.out / f"{svg.stem}.png", args.width)]
        if not args.no_hidpi:
            targets.append((args.out / f"{svg.stem}@2x.png", 2 * args.width))
        for png, width in targets:
            w, h = render_svg(svg, png, width)
            shown = png.relative_to(REPO) if png.is_relative_to(REPO) else png
            print(f"{svg.name} -> {shown} ({w}x{h})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
