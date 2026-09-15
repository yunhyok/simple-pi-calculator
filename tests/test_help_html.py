"""Help lint (DESIGN §6, §8.13).

Checks, without Qt, that the Help pages in ``src/simple_pi_calculator/help``

* all exist (the §6.2 page list plus the troubleshooting page) and are reachable from ``index.html``;
* use only the QTextBrowser "Supported HTML Subset": allowed tags and attributes, no forbidden tags,
  CSS restricted to simple selectors and the supported properties (no display/flex/grid/position/float);
* are well formed (every non-void element closed in the right order);
* have a title, a UTF-8 meta tag and the common header line of links;
* only contain internal links and anchors that resolve, and images that exist as PNG with a width of
  at most 760 px (and the declared height matching the file);
* together document every message code of DESIGN Appendix A;

and that every diagram source in ``docs/diagrams`` has its rendered PNG (plus the @2x variant).
"""

from __future__ import annotations

import re
import struct
from collections import deque
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HELP_DIR = REPO / "src" / "simple_pi_calculator" / "help"
IMG_DIR = HELP_DIR / "img"
DIAGRAM_DIR = REPO / "docs" / "diagrams"

REQUIRED_PAGES = [
    "index.html",
    "getting_started.html",
    "input_stackup.html",
    "input_vias.html",
    "input_pwr.html",
    "input_decaps.html",
    "spice_models.html",
    "touchstone.html",
    "physics.html",
    "results.html",
    "project_file.html",
    "troubleshooting.html",
    "limitations.html",
    "references.html",
    "about.html",
]

HEADER_LINKS = [
    "index.html",
    "getting_started.html",
    "input_stackup.html",
    "physics.html",
    "references.html",
]

ALLOWED_TAGS = {
    "html", "head", "title", "meta", "style", "body",
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "br", "hr", "div", "span",
    "a", "b", "i", "u", "em", "strong", "code", "tt", "pre", "sub", "sup",
    "ul", "ol", "li", "dl", "dt", "dd",
    "table", "tr", "th", "td", "img", "blockquote", "font",
}
FORBIDDEN_TAGS = {
    "script", "svg", "iframe", "video", "audio", "math", "canvas", "object", "embed",
    "link", "nav", "section", "article", "header", "footer", "form", "input", "button",
}
VOID_TAGS = {"br", "hr", "img", "meta"}
HEAD_ONLY_TAGS = {"title", "meta", "style"}

GLOBAL_ATTRS = {"style", "class", "id"}
TAG_ATTRS = {
    "a": {"href", "name"},
    "img": {"src", "width", "height", "alt"},
    "td": {"colspan", "rowspan", "width"},
    "th": {"colspan", "rowspan", "width"},
    "table": {"border", "cellspacing", "cellpadding", "width"},
    "font": {"color"},
    "meta": {"charset"},
}

ALLOWED_CSS_PROPERTIES = {
    "color", "background-color", "font-family", "font-size", "font-weight", "font-style",
    "text-decoration", "text-align", "vertical-align",
    "margin", "margin-top", "margin-bottom", "margin-left", "margin-right",
    "padding", "border-width", "border-style", "border-color", "white-space",
}
FORBIDDEN_CSS_WORDS = ("display", "flex", "grid", "position", "float")
SELECTOR_RE = re.compile(r"^(?:[a-z][a-z0-9]*)?(?:[.#][A-Za-z_][A-Za-z0-9_-]*)?$")
MAX_IMG_WIDTH = 760

# DESIGN Appendix A: every code must be explained in the Help (troubleshooting index at least).
DOCUMENTED_CODES = """
E_XL_HEADER_NOT_FOUND E_XL_NUMBER E_XL_UNIT E_XL_FORMULA_NO_VALUE W_XL_DUP_COLUMN E_XL_BOOL W_XL_COLUMN_IGNORED
E_STACK_EMPTY E_STACK_LAYER_NUM E_STACK_LAYER_DUP W_STACK_LAYER_GAP E_STACK_THICKNESS E_STACK_DK
W_STACK_DF_MISSING E_STACK_DF W_STACK_SIGMA_RANGE W_STACK_METAL_DKDF W_STACK_ADJ_METAL
W_STACK_METAL_BETWEEN E_STACK_NO_DIELECTRIC
E_PWR_NAME_DUP E_PWR_LAYER_NOT_FOUND E_PWR_LAYER_NOT_METAL E_PWR_SAME_LAYER E_PWR_DIM
E_PWR_WIDTH_TOO_SMALL W_PWR_FAR_GND W_PWR_NO_DECAPS
E_DECAP_FILE_NOT_FOUND E_DECAP_FILE_TYPE E_DECAP_DISTANCE E_DREF_TOO_SMALL W_DECAP_TOO_CLOSE
W_DECAP_CLIPPED W_PORT_OVERLAP I_DUMMY_SINGLE
E_DIST_MODE E_DIST_SIGMA E_DIST_SEED W_DIST_SIGMA_LARGE I_DIST_SAMPLED
E_PROJECT_FORMAT E_PROJECT_NEWER I_PROJECT_MIGRATED W_PROJECT_UNKNOWN_KEY
W_AUTOSAVE_CORRUPT W_AUTOSAVE_RECOVERED_BACKUP W_AUTOSAVE_NEWER
E_VIA_ANTIPAD E_VIA_COUNT E_VIA_PITCH W_VIA_PITCH_SMALL W_VIA_ZERO_LENGTH
E_SPICE_UNSUPPORTED E_SPICE_EXPR E_SPICE_PARAM_REDEF E_SPICE_RECURSION E_SPICE_UNKNOWN_SUBCKT
E_SPICE_PIN_COUNT E_SPICE_NO_SUBCKT W_SPICE_OPTION_IGNORED W_SPICE_IGNORED W_SPICE_UNKNOWN_PARAM
W_SPICE_GLOBAL_GND W_SPICE_MULTI_TOP W_K_UNITY W_MNA_FLOATING
E_S2P_PARAM E_S2P_V2 E_S2P_FORMAT W_S2P_NOISE_IGNORED W_S2P_SINGULAR W_S2P_EXTRAP_LOW W_S2P_EXTRAP_HIGH
E_SWEEP_RANGE W_SWEEP_HIGH W_MODES_CAPPED E_SINGULAR
""".split()


# --------------------------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------------------------
class PageParser(HTMLParser):
    """Collects everything the lint needs from one page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self.tags: set[str] = set()
        self.stack: list[str] = []
        self.anchors: set[str] = set()
        self.links: list[str] = []
        self.images: list[dict[str, str | None]] = []
        self.styles: list[str] = []
        self.inline_styles: list[str] = []
        self.title = ""
        self.meta_charset: str | None = None
        self.first_p_links: list[str] | None = None
        self._in_style = False
        self._in_title = False
        self._p_depth_links: list[str] | None = None

    # structure --------------------------------------------------------------------------
    def _pos(self) -> str:
        line, col = self.getpos()
        return f"line {line}"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, self_closing=True)

    def _start(self, tag: str, attrs: list[tuple[str, str | None]], self_closing: bool) -> None:
        self.tags.add(tag)
        if tag in FORBIDDEN_TAGS:
            self.errors.append(f"{self._pos()}: forbidden tag <{tag}>")
        elif tag not in ALLOWED_TAGS:
            self.errors.append(f"{self._pos()}: tag <{tag}> is not in the QTextBrowser subset")
        if tag in HEAD_ONLY_TAGS and "head" not in self.stack:
            self.errors.append(f"{self._pos()}: <{tag}> must be inside <head>")

        allowed = GLOBAL_ATTRS | TAG_ATTRS.get(tag, set())
        amap: dict[str, str | None] = {}
        for name, value in attrs:
            amap[name] = value
            if name.startswith("on"):
                self.errors.append(f"{self._pos()}: event attribute {name!r} on <{tag}>")
            elif name not in allowed:
                self.errors.append(f"{self._pos()}: attribute {name!r} not allowed on <{tag}>")
        if "style" in amap and amap["style"] is not None:
            self.inline_styles.append(amap["style"])
        if "id" in amap and amap["id"]:
            self.anchors.add(amap["id"])

        if tag == "a":
            if amap.get("name"):
                self.anchors.add(amap["name"])  # type: ignore[arg-type]
            if amap.get("href"):
                self.links.append(amap["href"])  # type: ignore[arg-type]
                if self._p_depth_links is not None:
                    self._p_depth_links.append(amap["href"])  # type: ignore[arg-type]
        elif tag == "img":
            info = dict(amap)
            info["pos"] = self._pos()
            self.images.append(info)
        elif tag == "meta":
            self.meta_charset = (amap.get("charset") or "").lower() or None
        elif tag == "style":
            self._in_style = True
        elif tag == "title":
            self._in_title = True
        elif tag == "p" and self.first_p_links is None and self._p_depth_links is None and "body" in self.stack:
            self._p_depth_links = []

        if tag not in VOID_TAGS and not self_closing:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        if not self.stack:
            self.errors.append(f"{self._pos()}: stray </{tag}>")
            return
        if self.stack[-1] != tag:
            self.errors.append(f"{self._pos()}: </{tag}> closes <{self.stack[-1]}> (open: {self.stack})")
            if tag in self.stack:  # resynchronise
                while self.stack and self.stack[-1] != tag:
                    self.stack.pop()
                self.stack.pop()
            return
        self.stack.pop()
        if tag == "style":
            self._in_style = False
        elif tag == "title":
            self._in_title = False
        elif tag == "p" and self._p_depth_links is not None and self.first_p_links is None:
            self.first_p_links = self._p_depth_links
            self._p_depth_links = None

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self.styles.append(data)
        elif self._in_title:
            self.title += data

    def close(self) -> None:
        super().close()
        if self.stack:
            self.errors.append(f"unclosed elements at end of file: {self.stack}")


def parse_page(path: Path) -> PageParser:
    parser = PageParser()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return parser


_PARSED: dict[str, PageParser] = {}


def parsed(name: str) -> PageParser:
    if name not in _PARSED:
        _PARSED[name] = parse_page(HELP_DIR / name)
    return _PARSED[name]


def check_declarations(text: str, where: str) -> list[str]:
    """Validate a CSS declaration list such as 'color: red; font-size: 9pt'."""
    problems = []
    for decl in text.split(";"):
        decl = decl.strip()
        if not decl:
            continue
        if ":" not in decl:
            problems.append(f"{where}: malformed declaration {decl!r}")
            continue
        prop, value = (s.strip().lower() for s in decl.split(":", 1))
        if any(word in prop for word in FORBIDDEN_CSS_WORDS):
            problems.append(f"{where}: forbidden CSS property {prop!r}")
        elif prop not in ALLOWED_CSS_PROPERTIES:
            problems.append(f"{where}: CSS property {prop!r} is not supported by QTextBrowser")
        if "var(" in value or "url(" in value or "calc(" in value:
            problems.append(f"{where}: unsupported CSS value {value!r}")
        if prop == "font-size" and not re.fullmatch(r"\d+(\.\d+)?(px|pt)", value):
            problems.append(f"{where}: font-size must be in px or pt, got {value!r}")
        if any(word in value for word in ("flex", "grid")):
            problems.append(f"{where}: forbidden CSS value {value!r}")
    return problems


def check_stylesheet(css: str) -> list[str]:
    problems = []
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    if "@" in css:
        problems.append("at-rules (@media, @import, @font-face) are not supported")
    rules = re.findall(r"([^{}]*)\{([^{}]*)\}", css)
    leftover = re.sub(r"([^{}]*)\{([^{}]*)\}", "", css).strip()
    if leftover:
        problems.append(f"unparsable CSS: {leftover[:60]!r}")
    for selectors, body in rules:
        for sel in selectors.split(","):
            sel = sel.strip()
            if not sel or not SELECTOR_RE.match(sel):
                problems.append(f"unsupported CSS selector {sel!r} (only element, .class, #id)")
        problems += check_declarations(body, f"rule {selectors.strip()!r}")
    return problems


def png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as fh:
        head = fh.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG file"
    return struct.unpack(">II", head[16:24])


def help_pages() -> list[str]:
    return sorted(p.name for p in HELP_DIR.glob("*.html"))


# --------------------------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------------------------
def test_help_directory_exists() -> None:
    assert HELP_DIR.is_dir(), f"missing help directory {HELP_DIR}"


@pytest.mark.parametrize("name", REQUIRED_PAGES)
def test_required_page_exists(name: str) -> None:
    assert (HELP_DIR / name).is_file(), f"missing help page {name}"


def test_file_names_are_ascii_lowercase() -> None:
    for path in list(HELP_DIR.glob("*.html")) + list(IMG_DIR.glob("*")):
        assert re.fullmatch(r"[a-z0-9_]+(@2x)?\.(html|png)", path.name), f"bad file name {path.name}"


@pytest.mark.parametrize("name", help_pages())
def test_html_subset_and_structure(name: str) -> None:
    page = parsed(name)
    assert not page.errors, f"{name}:\n  " + "\n  ".join(page.errors)
    for required in ("html", "head", "title", "body", "h1"):
        assert required in page.tags, f"{name}: missing <{required}>"
    assert page.title.strip(), f"{name}: empty <title>"
    assert page.meta_charset == "utf-8", f"{name}: missing <meta charset=\"utf-8\">"


@pytest.mark.parametrize("name", help_pages())
def test_css_is_supported(name: str) -> None:
    page = parsed(name)
    problems: list[str] = []
    for css in page.styles:
        problems += check_stylesheet(css)
    for inline in page.inline_styles:
        problems += check_declarations(inline, "style attribute")
    assert not problems, f"{name}:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("name", help_pages())
def test_common_header_line(name: str) -> None:
    page = parsed(name)
    assert page.first_p_links is not None, f"{name}: no header paragraph"
    assert page.first_p_links == HEADER_LINKS, (
        f"{name}: first paragraph must be the header line {HEADER_LINKS}, got {page.first_p_links}"
    )


@pytest.mark.parametrize("name", help_pages())
def test_internal_links_resolve(name: str) -> None:
    page = parsed(name)
    problems = []
    for href in page.links:
        if re.match(r"^(https?|mailto):", href):
            continue
        if re.match(r"^[a-z]+:", href) or href.startswith(("/", "\\")) or "\\" in href:
            problems.append(f"link {href!r} must be a relative path with forward slashes")
            continue
        target, _, fragment = href.partition("#")
        target = target or name
        target_path = (HELP_DIR / target).resolve()
        if not target_path.is_file() or target_path.parent != HELP_DIR.resolve():
            problems.append(f"link {href!r}: page {target!r} does not exist")
            continue
        if not target.endswith(".html"):
            problems.append(f"link {href!r} must point to an .html page")
            continue
        if fragment and fragment not in parsed(target).anchors:
            problems.append(f"link {href!r}: anchor #{fragment} not found in {target}")
    assert not problems, f"{name}:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("name", help_pages())
def test_images_resolve(name: str) -> None:
    page = parsed(name)
    problems = []
    for img in page.images:
        where = f"{img['pos']} <img src={img.get('src')!r}>"
        src = img.get("src") or ""
        if not re.fullmatch(r"img/[a-z0-9_]+\.png", src):
            problems.append(f"{where}: src must be a relative img/<name>.png path")
            continue
        path = HELP_DIR / src
        if not path.is_file():
            problems.append(f"{where}: file not found")
            continue
        if not img.get("alt"):
            problems.append(f"{where}: missing alt text")
        width_attr = img.get("width")
        if not width_attr or not width_attr.isdigit():
            problems.append(f"{where}: explicit numeric width required")
            continue
        width = int(width_attr)
        if width > MAX_IMG_WIDTH:
            problems.append(f"{where}: width {width} > {MAX_IMG_WIDTH}")
        file_w, file_h = png_size(path)
        height_attr = img.get("height")
        if height_attr:
            expected = round(file_h * width / file_w)
            if not height_attr.isdigit() or abs(int(height_attr) - expected) > 1:
                problems.append(f"{where}: height {height_attr} does not match the PNG (expected {expected})")
        if file_w != width:
            problems.append(
                f"{where}: PNG is {file_w} px wide but shown at {width} px; QTextBrowser scales without "
                "smoothing - render at the display width (tools/render_diagrams.py)"
            )
    assert not problems, f"{name}:\n  " + "\n  ".join(problems)


def test_all_pages_reachable_from_index() -> None:
    seen = {"index.html"}
    queue = deque(["index.html"])
    while queue:
        current = queue.popleft()
        for href in parsed(current).links:
            if re.match(r"^[a-z]+:", href):
                continue
            target = href.partition("#")[0] or current
            if target not in seen and (HELP_DIR / target).is_file():
                seen.add(target)
                queue.append(target)
    unreachable = sorted(set(help_pages()) - seen)
    assert not unreachable, f"pages not reachable from index.html: {unreachable}"


def test_every_image_is_used() -> None:
    used = {img.get("src") for name in help_pages() for img in parsed(name).images}
    for png in IMG_DIR.glob("*.png"):
        if png.stem.endswith("@2x"):
            base = IMG_DIR / (png.stem[:-3] + ".png")
            assert base.is_file(), f"{png.name} has no base image {base.name}"
            continue
        assert f"img/{png.name}" in used, f"image {png.name} is not referenced by any page"


def test_diagram_sources_are_rendered() -> None:
    if not DIAGRAM_DIR.is_dir():
        pytest.skip("docs/diagrams not present (installed package)")
    svgs = sorted(DIAGRAM_DIR.glob("*.svg"))
    assert svgs, "no SVG diagram sources found"
    for svg in svgs:
        text = svg.read_text(encoding="utf-8")
        assert "<svg" in text and "</svg>" in text, f"{svg.name} is not a complete SVG document"
        for suffix in (".png", "@2x.png"):
            png = IMG_DIR / f"{svg.stem}{suffix}"
            assert png.is_file(), f"{svg.name} not rendered to {png.name}; run tools/render_diagrams.py"
            assert png.stat().st_mtime >= svg.stat().st_mtime - 2, (
                f"{png.name} is older than {svg.name}; run tools/render_diagrams.py"
            )


def test_message_codes_are_documented() -> None:
    all_text = "\n".join((HELP_DIR / name).read_text(encoding="utf-8") for name in help_pages())
    index_text = (HELP_DIR / "troubleshooting.html").read_text(encoding="utf-8")
    missing = [code for code in DOCUMENTED_CODES if code not in all_text]
    assert not missing, f"message codes not explained in the Help: {missing}"
    missing_index = [code for code in DOCUMENTED_CODES if code not in index_text]
    assert not missing_index, f"codes missing from the troubleshooting index: {missing_index}"
