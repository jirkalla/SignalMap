"""Extract readable text (and where on the page it came from) from a captured HTML page or PDF.

docs/TASKS_CITATION_VERIFICATION.md T4, design decisions 10-11. Two independent extractors,
both pure functions over already-downloaded bytes — network I/O lives in app/services/
source_capture.py, not here, so both are testable with plain fixture files.

`extract_html` is a deliberately hand-rolled parser (design decision 10), not `trafilatura` or
another "readability" library: those discard exactly the content this project most needs —
boilerplate-looking and collapsed sections — because a provider has been observed citing text
from a closed accordion panel (leichtbau-bw.eu's "Bauwesen" section, verified against the
citation-verification prototype, docs/TASKS_CITATION_VERIFICATION.md). A "readability" pass would
have silently dropped exactly that citation's source text.

The `locations` shape (`start`, `end`, `headings`, `collapsed`, `collapsed_title`) is not invented
here — it is copied from the prototype's own stored output (claude.ai artifact
39VLVcB4u27cvpv87PRcvs, read 2026-09-28), e.g. the leichtbau-bw.eu match above carries
`{"headings": ["Leichtbau in BW", "Wichtige Branchen und Themenfelder im Leichtbau"],
"collapsed": true, "collapsed_title": "Bauwesen"}` — reusing that shape means T8's quote_match and
T9's "open at this location" link can rely on a format already exercised against real pages.
"""

import io
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

import pypdf

# Content never contributes to extracted text (design decision 10) — a provider cannot cite what
# a browser never renders as text, and these tags' "content" isn't text in that sense anyway
# (script/style source code, an unsupported-browser fallback, vector markup, an inert template).
_SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}

_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

# Void elements: HTMLParser calls handle_starttag for these but never handle_endtag (no closing
# tag exists in real-world HTML), so they must never be pushed onto the open-element stack —
# doing so would leave the stack permanently unbalanced the first time a real container tag closes.
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
    "track", "wbr",
}

_DISPLAY_NONE_RE = re.compile(r"display\s*:\s*none", re.IGNORECASE)


@dataclass(frozen=True)
class ExtractedHtml:
    """The result of `extract_html`: the page's full text (visible and hidden/collapsed), plus

    `locations` — a list of `{start, end, headings, collapsed, collapsed_title}` records, each
    describing one contiguous run of `text[start:end]` (adjacent runs with identical headings/
    collapsed state are merged, so this is not one record per raw HTML text node).
    """

    text: str
    locations: list[dict[str, Any]]


class _Frame:
    """One open element on the parser's stack — only the state a descendant text node needs to

    know about its ancestors: is this element (or one already above it) collapsed, and if so,
    under what title.
    """

    __slots__ = ("tag", "collapsed", "collapsed_title", "collapsed_title_pending", "force_visible")

    def __init__(
        self,
        tag: str,
        *,
        collapsed: bool,
        collapsed_title: str | None,
        pending_summary_title: bool,
        force_visible: bool = False,
    ):
        self.tag = tag
        self.collapsed = collapsed
        self.collapsed_title = collapsed_title
        # True only for <summary> — it stops collapse from propagating down from its own
        # <details> parent (a <summary> is always visible; it's what a reader clicks to reveal
        # the rest), without also hiding a collapsed ancestor further up from reasserting itself
        # once this frame is popped again.
        self.force_visible = force_visible
        # True only for a <details> frame that ITSELF triggered the collapse (not one merely
        # nested inside an already-collapsed ancestor) and has no title yet — resolved in place
        # once its <summary> child's text is captured. A <summary> appearing anywhere among the
        # details' children (always first in practice) retroactively titling text already
        # emitted before it was seen is NOT supported — real pages put <summary> first, and
        # handling the out-of-order case would need a second pass for no observed real-world
        # benefit.
        self.collapsed_title_pending = pending_summary_title


class _HtmlTextExtractor(HTMLParser):
    """Single-pass HTML -> (text, locations). See module docstring for the collapse/heading rules.

    Two-pass only for `aria-labelledby`: `_id_text` is populated by a first pass
    (`_IdTextCollector` below) before this parser runs, so a hidden element's title can reference
    an id anywhere in the document, not only one already seen.
    """

    def __init__(self, id_text: dict[str, str]):
        super().__init__(convert_charrefs=True)
        self._id_text = id_text
        self._stack: list[_Frame] = []
        self._text_parts: list[str] = []
        self._length = 0
        self._locations: list[dict[str, Any]] = []
        self._heading_stack: list[tuple[int, str]] = []
        self._heading_capture: list[str] | None = None
        self._heading_capture_level: int | None = None
        self._heading_capture_depth: int | None = None
        self._summary_capture: list[str] | None = None
        self._summary_capture_depth: int | None = None

    @property
    def result(self) -> ExtractedHtml:
        return ExtractedHtml(text="".join(self._text_parts), locations=self._locations)

    def _current_collapsed(self, stack: list["_Frame"] | None = None) -> tuple[bool, str | None]:
        for frame in reversed(self._stack if stack is None else stack):
            if frame.force_visible:
                return False, None
            if frame.collapsed:
                return True, frame.collapsed_title
        return False, None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._open_tag(tag, attrs)
        if tag in _VOID_TAGS:
            return
        # <tag/> in XHTML-style markup — HTMLParser calls handle_startendtag, whose default
        # implementation is exactly starttag+endtag, so this branch is never reached for those;
        # only genuinely unclosed void tags stay off the stack.

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._open_tag(tag, attrs)
        if tag not in _VOID_TAGS:
            self._close_tag(tag)

    def _open_tag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name: (value or "") for name, value in attrs}
        already_collapsed, inherited_title = self._current_collapsed()
        collapsed = already_collapsed
        title = inherited_title
        self_collapsed = False
        # A new collapsing trigger on THIS element — never on one already inside a collapsed
        # ancestor, whose title already wins (design decision 10 tracks the nearest one).
        if not already_collapsed:
            if "hidden" in attr_map:
                collapsed = True
            elif attr_map.get("aria-hidden", "").strip().lower() == "true":
                collapsed = True
            elif _DISPLAY_NONE_RE.search(attr_map.get("style", "")):
                collapsed = True
            elif tag == "details" and "open" not in attr_map:
                collapsed = True
            if collapsed:
                self_collapsed = True
                labelledby = attr_map.get("aria-labelledby", "").strip()
                if labelledby:
                    title = self._id_text.get(labelledby.split()[0])
        if tag in _VOID_TAGS:
            # Not pushed onto the stack (no matching end tag will ever pop it), but a void
            # element can itself carry hidden/aria-hidden — irrelevant here since void elements
            # never contain text, so there is nothing further to track for it.
            return
        pending_summary_title = self_collapsed and tag == "details" and title is None
        frame = _Frame(
            tag,
            collapsed=collapsed,
            collapsed_title=title,
            pending_summary_title=pending_summary_title,
            force_visible=(tag == "summary"),
        )
        self._stack.append(frame)

        if tag == "summary" and self._summary_capture is None:
            self._summary_capture = []
            self._summary_capture_depth = len(self._stack)
        elif tag in _HEADING_TAGS and self._heading_capture is None:
            self._heading_capture = []
            self._heading_capture_level = int(tag[1])
            self._heading_capture_depth = len(self._stack)

    def handle_endtag(self, tag: str) -> None:
        self._close_tag(tag)

    def _close_tag(self, tag: str) -> None:
        # Lenient close: find the nearest matching open frame and pop everything from there up —
        # real-world HTML is not always well-formed, and a stray/mismatched end tag (no matching
        # open frame at all) is simply ignored rather than corrupting the stack.
        index = None
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i].tag == tag:
                index = i
                break
        if index is None:
            return

        if self._summary_capture is not None and self._summary_capture_depth == index + 1:
            title = "".join(self._summary_capture).strip()
            self._summary_capture = None
            self._summary_capture_depth = None
            if title:
                # The nearest enclosing <details> waiting for its <summary> title — walk
                # outward from the summary's own position, since <summary> itself sits one
                # level inside the <details> it titles.
                for frame in reversed(self._stack[:index]):
                    if frame.collapsed_title_pending:
                        frame.collapsed_title = title
                        frame.collapsed_title_pending = False
                        break

        if self._heading_capture is not None and self._heading_capture_depth == index + 1:
            heading_text = "".join(self._heading_capture).strip()
            level = self._heading_capture_level
            self._heading_capture = None
            self._heading_capture_level = None
            self._heading_capture_depth = None
            if heading_text:
                while self._heading_stack and self._heading_stack[-1][0] >= level:
                    self._heading_stack.pop()
                self._heading_stack.append((level, heading_text))

        del self._stack[index:]

    def handle_data(self, data: str) -> None:
        if any(frame.tag in _SKIP_TAGS for frame in self._stack):
            return
        if self._summary_capture is not None:
            self._summary_capture.append(data)
        if self._heading_capture is not None:
            self._heading_capture.append(data)

        if self._text_parts and not self._text_parts[-1][-1:].isspace() and not data[:1].isspace():
            self._text_parts.append(" ")
            self._length += 1

        start = self._length
        self._text_parts.append(data)
        self._length += len(data)
        end = self._length
        if start == end:
            return

        collapsed, collapsed_title = self._current_collapsed()
        headings = tuple(text for _, text in self._heading_stack)
        last = self._locations[-1] if self._locations else None
        if (
            last is not None
            and last["end"] == start
            and tuple(last["headings"]) == headings
            and last["collapsed"] == collapsed
            and last["collapsed_title"] == collapsed_title
        ):
            last["end"] = end
        else:
            self._locations.append(
                {
                    "start": start,
                    "end": end,
                    "headings": list(headings),
                    "collapsed": collapsed,
                    "collapsed_title": collapsed_title,
                }
            )


class _IdTextCollector(HTMLParser):
    """First pass: `id -> visible text content` for every element with an `id` attribute, so

    `aria-labelledby` can be resolved regardless of where in the document the referenced element
    appears (unlike `<summary>`, an `aria-labelledby` target is not guaranteed to appear before
    the hidden element that references it).
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._stack: list[tuple[str, str | None]] = []  # (tag, id)
        self._buffers: dict[str, list[str]] = {}

    @property
    def result(self) -> dict[str, str]:
        return {id_: "".join(parts).strip() for id_, parts in self._buffers.items()}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID_TAGS:
            return
        element_id = next((value for name, value in attrs if name == "id" and value), None)
        if element_id:
            self._buffers.setdefault(element_id, [])
        self._stack.append((tag, element_id))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        pass  # self-closing elements never carry text content worth indexing here.

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if any(t in _SKIP_TAGS for t, _ in self._stack):
            return
        for _, element_id in self._stack:
            if element_id:
                self._buffers[element_id].append(data)


def extract_html(html: str) -> ExtractedHtml:
    """Extract visible-and-hidden text plus per-run location metadata from one HTML page.

    Never raises on malformed markup — `html.parser.HTMLParser` is lenient by design, and this
    extractor's own tag-stack handling silently ignores stray/mismatched end tags rather than
    erroring (real captured pages are not guaranteed well-formed).
    """
    id_collector = _IdTextCollector()
    id_collector.feed(html)
    id_collector.close()

    extractor = _HtmlTextExtractor(id_collector.result)
    extractor.feed(html)
    extractor.close()
    return extractor.result


# A PDF line-wrapped hyphenation ("Techno-\nlogien" -> "Technologien") — verified against a real
# stored citation (docs/TASKS_CITATION_VERIFICATION.md's Bundestag PDF example, run 108 citation
# #5: "Techno- logien wie den Leichtbau" in the provider's own cited_text).
_HYPHEN_LINEBREAK_RE = re.compile(r"(\w)-\n(\w)")


@dataclass(frozen=True)
class ExtractedPdf:
    """The result of `extract_pdf`: concatenated page text plus `page_starts`, the character

    offset in `text` where each page's own content begins (a blank-line separator between pages
    is inserted so the last word of one page never fuses with the first word of the next, and
    counted into the FOLLOWING page's start — not into the previous page's own span).
    """

    text: str
    page_starts: list[int]


def extract_pdf(data: bytes) -> ExtractedPdf | None:
    """Extract text page-by-page from a PDF's bytes, or None when it has no text layer at all

    (`error_reason='pdf_no_text'` is the caller's job to record — a scanned PDF with no OCR is a
    known, permanent limitation, design decision 11, not something to raise an exception over).
    """
    reader = pypdf.PdfReader(io.BytesIO(data))
    pages = [_HYPHEN_LINEBREAK_RE.sub(r"\1\2", page.extract_text() or "") for page in reader.pages]

    parts: list[str] = []
    page_starts: list[int] = []
    offset = 0
    for index, page_text in enumerate(pages):
        if index > 0:
            parts.append("\n\n")
            offset += 2
        page_starts.append(offset)
        parts.append(page_text)
        offset += len(page_text)

    text = "".join(parts)
    if not text.strip():
        return None
    return ExtractedPdf(text=text, page_starts=page_starts)
