"""Tests for the deterministic main.ptx project-manifest assembly.

The assembler's job is to wire per-chapter fragments into one buildable
PreTeXt project via ``<xi:include>`` under a ``<book>``/``<article>`` root —
so the invariants worth pinning are: the right root for the project shape, a
well-formed ``<pretext>`` document, and every chapter included in manifest
order.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from lyretext.render import build_main_ptx, resolve_root
from lyretext.render.assemble import _ncname

XI = "{http://www.w3.org/2001/XInclude}include"


def _manifest(*entries: tuple[str, str, str]) -> list[dict]:
    """Build a manifest from (type, name, output_path) triples."""
    return [
        {"type": t, "name": n, "output_path": p, "source_path": p}
        for (t, n, p) in entries
    ]


CHAPTERS = _manifest(
    ("chapter", "Introduction", "output/chapter-1-introduction.ptx"),
    ("chapter", "Measuring Spread", "output/chapter-2-measuring-spread.ptx"),
    ("chapter", "Conclusions", "output/chapter-3-conclusions.ptx"),
)


def test_output_is_well_formed_pretext_with_xinclude_namespace():
    root = ET.fromstring(build_main_ptx(CHAPTERS, title="Stats"))
    assert root.tag == "pretext"
    # xmlns:xi must be declared or the include elements below wouldn't parse
    # into the XInclude namespace.
    assert root.find("book") is not None


def test_book_root_when_manifest_has_chapters():
    assert resolve_root(CHAPTERS, "auto") == "book"
    root = ET.fromstring(build_main_ptx(CHAPTERS, title="Stats"))
    book = root.find("book")
    assert book is not None
    assert book.findtext("title") == "Stats"


def test_every_chapter_included_in_manifest_order():
    root = ET.fromstring(build_main_ptx(CHAPTERS, title="Stats"))
    book = root.find("book")
    # book root wraps each <section> fragment in a generated <chapter>.
    hrefs = [inc.get("href") for inc in book.iter(XI)]
    assert hrefs == [
        "chapter-1-introduction.ptx",
        "chapter-2-measuring-spread.ptx",
        "chapter-3-conclusions.ptx",
    ]
    # Each include sits inside a titled <chapter> wrapper (book > chapter >
    # section is the valid shape for our section-rooted fragments).
    chapters = book.findall("chapter")
    assert len(chapters) == 3
    assert chapters[0].findtext("title") == "Introduction"
    assert chapters[0].find(XI).get("href") == "chapter-1-introduction.ptx"


def test_hrefs_are_bare_filenames_not_full_paths():
    # main.ptx lives in the output dir alongside the fragments, so includes
    # must be relative filenames — an absolute/nested path would not resolve.
    root = ET.fromstring(build_main_ptx(CHAPTERS, title="Stats"))
    for inc in root.iter(XI):
        assert "/" not in inc.get("href")
        assert "\\" not in inc.get("href")


def test_article_root_includes_sections_directly_without_chapter_wrappers():
    root = ET.fromstring(build_main_ptx(CHAPTERS, title="Paper", root_element="article"))
    article = root.find("article")
    assert article is not None
    assert article.findall("chapter") == []
    hrefs = [inc.get("href") for inc in article.findall(XI)]
    assert hrefs == [
        "chapter-1-introduction.ptx",
        "chapter-2-measuring-spread.ptx",
        "chapter-3-conclusions.ptx",
    ]


def test_auto_root_is_article_when_no_chapters():
    fm = _manifest(("front_matter", "Preface", "output/preface.ptx"))
    assert resolve_root(fm, "auto") == "article"


def test_front_and_back_matter_are_placed_in_their_wrappers_in_order():
    manifest = _manifest(
        ("front_matter", "Preface", "output/preface.ptx"),
        ("chapter", "One", "output/chapter-1.ptx"),
        ("chapter", "Two", "output/chapter-2.ptx"),
        ("back_matter", "Index", "output/index.ptx"),
    )
    root = ET.fromstring(build_main_ptx(manifest, title="Book", root_element="book"))
    book = root.find("book")

    front = book.find("frontmatter")
    back = book.find("backmatter")
    assert front is not None and back is not None
    assert [inc.get("href") for inc in front.findall(XI)] == ["preface.ptx"]
    assert [inc.get("href") for inc in back.findall(XI)] == ["index.ptx"]

    # Body chapters sit directly under book, between front and back matter.
    assert [c.find(XI).get("href") for c in book.findall("chapter")] == [
        "chapter-1.ptx",
        "chapter-2.ptx",
    ]


def test_explicit_root_overrides_auto_detection():
    assert resolve_root(CHAPTERS, "article") == "article"
    assert resolve_root([], "book") == "book"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("chapter-1-introduction", "chapter-1-introduction"),
        ("1 leading digit", "ch-1-leading-digit"),
        ("spaces & symbols!", "spaces-symbols"),
        ("", "ch"),
    ],
)
def test_ncname_sanitisation(raw, expected):
    assert _ncname(raw, fallback="ch") == expected


def test_xml_id_is_a_valid_ncname_even_for_awkward_filenames():
    manifest = _manifest(("chapter", "7 Wonders", "output/7 wonders & more.ptx"))
    root = ET.fromstring(build_main_ptx(manifest, title="X", root_element="book"))
    chap = root.find("book").find("chapter")
    xml_id = chap.get("{http://www.w3.org/XML/1998/namespace}id")
    assert xml_id and xml_id[0].isalpha()
    assert " " not in xml_id
