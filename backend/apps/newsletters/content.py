"""
Newsletter HTML: cleaning what admins write, and the plain-text copy.

The body comes from a rich-text editor and is shown both in emails and
inside the portal, so it's sanitised to a safe subset of HTML: no
scripts, event handlers, iframes, forms or inline styles (the email
template provides the styling).
"""

import html
import re

import nh3


ALLOWED_TAGS = {
    "p", "br", "hr",
    "h1", "h2", "h3", "h4",
    "strong", "b", "em", "i", "u", "s",
    "a", "img",
    "ul", "ol", "li",
    "blockquote",
    "table", "thead", "tbody", "tr", "th", "td",
}

ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "width", "height"},
    "th": {"colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
}

ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}


def sanitize(body_html):
    return nh3.clean(
        body_html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer",
    ).strip()


def has_text(body_html):
    """
    True if the HTML shows something: words or an image.
    """
    return bool(to_text(body_html).strip()) or "<img" in body_html


# Paragraph-level blocks get a blank line after them; lines within a
# block (list items, table rows, <br>) just a line break.
_PARAGRAPH_END = re.compile(r"</(p|h[1-4]|blockquote|ul|ol|table)>|<hr\s*/?>", re.I)
_LINE_END = re.compile(r"</(li|tr)>|<br\s*/?>", re.I)
_LIST_ITEM = re.compile(r"<li[^>]*>", re.I)
_LINK = re.compile(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_BLANK_LINES = re.compile(r"\n{3,}")


def to_text(body_html):
    """
    Readable plain text for email clients that don't show HTML (and for
    spam filters, which distrust HTML-only mail).
    """
    text = _LINK.sub(
        lambda m: m.group(2) if m.group(2) == m.group(1) else f"{m.group(2)} ({m.group(1)})",
        body_html,
    )
    text = _LIST_ITEM.sub("• ", text)
    text = _PARAGRAPH_END.sub("\n\n", text)
    text = _LINE_END.sub("\n", text)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    text = "\n".join(line.strip() for line in text.splitlines())

    return _BLANK_LINES.sub("\n\n", text).strip()
