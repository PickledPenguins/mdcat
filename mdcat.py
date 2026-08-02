#!/usr/bin/env python3
"""mdcat - render a Markdown file to the terminal.

Zero dependencies (standard library only). Supports headers, emphasis,
inline code, fenced and indented code blocks (with light bash/Python
highlighting plus line numbers), nested/ordered/task lists, blockquotes,
pipe tables, horizontal rules, links, images, ==highlight==,
~~strikethrough~~, and flat key:value frontmatter.

The pipeline has three stages, in this order:

    raw text  ->  _prepare_lines()  ->  parse_blocks()  ->  render_blocks()
                  (sanitise input)     (text -> AST)       (AST -> ANSI lines)

Keeping those stages separate is what makes the tool testable: every stage
takes plain data in and returns plain data out, so a test can check the AST
without going anywhere near a terminal.
"""
import argparse
import errno
import os
import re
import shlex
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

VERSION = "0.2"

# Narrowest width the renderer will lay out for. Below roughly this many
# columns tables and code panels stop being readable at all, so both the
# --width flag and the config file refuse smaller values.
MIN_WIDTH = 20

# Fallbacks used when the terminal size cannot be detected (for example when
# output is piped into another program rather than shown on a screen).
DEFAULT_WIDTH = 100
DEFAULT_HEIGHT = 24

# Nested content (list bodies, quoted blocks, panels inside them) is laid out
# inside a reduced width. This floor stops deep nesting from shrinking it to
# nothing. It is deliberately small: a floor larger than the indent it sits
# inside would push nested content back past the right edge.
MIN_BODY_WIDTH = 4

# A tab is expanded to this many columns before anything is measured, so the
# renderer only ever has to reason about spaces.
TAB_WIDTH = 4

# Frontmatter is meant to be a short header. A document that opens with '---'
# and then runs on for longer than this is treated as ordinary content.
MAX_FRONTMATTER_LINES = 200


# ============================================================
# ANSI / theming
# ============================================================
# Attribute "unset" codes (22/23/24/29/39/49) are used instead of a blanket
# reset (0) when closing an inline span, so a nested style (e.g. a code
# span inside bold) doesn't clobber the *outer* style when it ends. A full
# reset is only used at the end of a whole rendered line as a safety net.
#
# Note the one asymmetry worth knowing about: SGR 22 clears bold *and* dim,
# because the terminal spec gives them no separate unset codes. Any style
# that may appear nested inside bold therefore uses colour rather than dim,
# which can be closed precisely with 39.
SGR = {
    "bold": "1", "unbold": "22",
    "dim": "2", "undim": "22",
    "italic": "3", "unitalic": "23",
    "underline": "4", "ununderline": "24",
    "strike": "9", "unstrike": "29",
    "fg_default": "39", "bg_default": "49",
}
RESET = "\x1b[0m"


def sgr(*codes):
    """Build an ANSI escape sequence from one or more SGR parameter strings."""
    return f"\x1b[{';'.join(codes)}m"


THEMES = {
    "dark": {
        "h1": sgr(SGR["bold"], "38;5;39"), "h2": sgr(SGR["bold"], "38;5;45"),
        "h_other": sgr(SGR["bold"], "38;5;51"),
        "bold": sgr(SGR["bold"]), "italic": sgr(SGR["italic"]),
        "strike": sgr(SGR["strike"]), "highlight": sgr("30", "48;5;227"),
        "code_inline": sgr("38;5;217", "48;5;236"), "code_bg": "48;5;236",
        "code_comment": sgr(SGR["dim"], "38;5;108"), "code_string": sgr("38;5;180"),
        "code_keyword": sgr(SGR["bold"], "38;5;75"), "code_lineno": sgr("38;5;244"),
        "quote": sgr(SGR["italic"], "38;5;109"), "quote_bar": sgr("38;5;109"),
        "rule": sgr("38;5;240"),
        "table_border": sgr("38;5;240"), "table_header": sgr(SGR["bold"]),
        "link_text": sgr(SGR["underline"], "38;5;75"), "link_url": sgr("38;5;244"),
        "fm_key": sgr(SGR["bold"], "38;5;108"), "fm_value": sgr("38;5;250"),
        "bullet": sgr("38;5;75"),
    },
    "light": {
        "h1": sgr(SGR["bold"], "38;5;18"), "h2": sgr(SGR["bold"], "38;5;25"),
        "h_other": sgr(SGR["bold"], "38;5;30"),
        "bold": sgr(SGR["bold"]), "italic": sgr(SGR["italic"]),
        "strike": sgr(SGR["strike"]), "highlight": sgr("30", "48;5;222"),
        "code_inline": sgr("38;5;88", "48;5;253"), "code_bg": "48;5;253",
        "code_comment": sgr(SGR["dim"], "38;5;28"), "code_string": sgr("38;5;94"),
        "code_keyword": sgr(SGR["bold"], "38;5;18"), "code_lineno": sgr("38;5;242"),
        "quote": sgr(SGR["italic"], "38;5;24"), "quote_bar": sgr("38;5;24"),
        "rule": sgr("38;5;250"),
        "table_border": sgr("38;5;250"), "table_header": sgr(SGR["bold"]),
        "link_text": sgr(SGR["underline"], "38;5;18"), "link_url": sgr("38;5;102"),
        "fm_key": sgr(SGR["bold"], "38;5;28"), "fm_value": sgr("38;5;238"),
        "bullet": sgr("38;5;18"),
    },
}

# Two interchangeable sets of drawing characters. The ASCII set is used when
# the output encoding cannot represent box-drawing glyphs -- a real situation
# on machines running under LANG=C, where emitting them would otherwise be an
# encoding error rather than a cosmetic problem.
GLYPHS_UNICODE = {
    "g_hbar": "\u2500", "g_vbar": "\u2502", "g_dbar": "\u2550",
    "g_tl": "\u250c", "g_tm": "\u252c", "g_tr": "\u2510",
    "g_ml": "\u251c", "g_mm": "\u253c", "g_mr": "\u2524",
    "g_bl": "\u2514", "g_bm": "\u2534", "g_br": "\u2518",
    "g_bullet": "\u2022", "g_circle": "\u25e6",
    "g_checked": "\u2611", "g_unchecked": "\u2610",
    "g_quote": "\u2502", "g_cont": "\u21b3",
}
GLYPHS_ASCII = {
    "g_hbar": "-", "g_vbar": "|", "g_dbar": "=",
    "g_tl": "+", "g_tm": "+", "g_tr": "+",
    "g_ml": "+", "g_mm": "+", "g_mr": "+",
    "g_bl": "+", "g_bm": "+", "g_br": "+",
    "g_bullet": "*", "g_circle": "-",
    "g_checked": "[x]", "g_unchecked": "[ ]",
    "g_quote": ">", "g_cont": ">",
}


def build_theme(name="dark", ascii_only=False):
    """Return a render theme: the colour codes for `name` plus a glyph set.

    Themes are passed down through every renderer, so bundling the glyphs
    into the same dictionary means the drawing-character choice travels with
    the colours instead of living in a global variable.
    """
    theme = dict(THEMES.get(name, THEMES["dark"]))
    theme.update(GLYPHS_ASCII if ascii_only else GLYPHS_UNICODE)
    return theme


# ============================================================
# Text measurement
# ============================================================
# Matches the SGR escape sequences this program emits. Input sanitisation
# (below) removes every escape sequence from the source document, so anything
# this pattern finds later is always something the renderer itself produced.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text):
    """Remove SGR escape sequences, leaving only the characters a reader sees."""
    return _ANSI_RE.sub("", text)


def _char_width(char):
    """Return how many terminal columns a single character occupies."""
    if unicodedata.combining(char):
        return 0  # accents and similar marks draw on top of the previous cell
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def _display_width(text):
    """Return the column count of plain text (no escape sequences expected)."""
    return sum(_char_width(char) for char in text)


def _visible_len(text):
    """Return the column count of styled text, ignoring its escape sequences."""
    return _display_width(_strip_ansi(text))


def _chunk_styled(text, width):
    """Split styled text into pieces of at most `width` columns each.

    Escape sequences are copied through without counting toward the width,
    so a chunk boundary never lands in the middle of one and split it in half.
    """
    width = max(width, 1)
    chunks, current, current_len, i = [], [], 0, 0
    while i < len(text):
        match = _ANSI_RE.match(text, i)
        if match:
            current.append(match.group(0))
            i = match.end()
            continue
        char_width = _char_width(text[i])
        if current_len and current_len + char_width > width:
            chunks.append("".join(current))
            current, current_len = [], 0
        current.append(text[i])
        current_len += char_width
        i += 1
    if current:
        chunks.append("".join(current))
    return chunks or [""]


# ============================================================
# Input sanitisation
# ============================================================
# Escape sequences in a source document are stripped rather than displayed.
# A Markdown file is data, not a program: letting it drive the terminal
# directly would let a file clear the screen, rewrite the window title, or
# forge styled output that looks like it came from the renderer.
_ESC_SEQ_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"   # OSC ... terminated by BEL or ST
    r"|\x1b\[[0-?]*[ -/]*[@-~]"            # CSI ... (colour, cursor moves, erase)
    r"|\x1bP[^\x1b]*\x1b\\"                # DCS ...
    r"|\x1b."                              # any remaining two-character escape
)
# Remaining control characters are dropped too. NUL and SOH in particular are
# used internally as placeholder markers while rendering, so they must not be
# able to reach the renderer from a file.
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u0080-\u009f]")


def _sanitize(line):
    """Strip terminal escape sequences and stray control characters from a line."""
    return _CTRL_RE.sub("", _ESC_SEQ_RE.sub("", line))


def _prepare_lines(text):
    """Turn raw document text into the clean list of lines the parser expects."""
    return [_sanitize(line.expandtabs(TAB_WIDTH)) for line in text.splitlines()]


# ============================================================
# Block-level AST
# ============================================================
@dataclass
class Heading:
    level: int
    text: str


@dataclass
class Para:
    text: str


@dataclass
class ListItem:
    text: str
    level: int
    ordered: bool
    number: int = 0
    checked: Optional[bool] = None  # None = not a task item
    # Blocks nested underneath the item, e.g. a fenced code sample. Stored as
    # a block list so the ordinary renderer can lay them out recursively.
    children: List = field(default_factory=list)


@dataclass
class ListBlock:
    items: List[ListItem] = field(default_factory=list)


@dataclass
class CodeBlock:
    lang: str
    lines: List[str] = field(default_factory=list)


@dataclass
class Quote:
    # A blockquote holds parsed blocks, not raw text, so a list or code sample
    # inside a quote keeps its structure instead of collapsing into one line.
    blocks: List = field(default_factory=list)


@dataclass
class Table:
    header: List[str]
    aligns: List[str]  # 'l' | 'r' | 'c'
    rows: List[List[str]] = field(default_factory=list)


@dataclass
class Rule:
    pass


@dataclass
class FrontMatter:
    pairs: List[Tuple[str, str]] = field(default_factory=list)


# ============================================================
# Block-level parser
# ============================================================
_FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*(\S*)\s*$")
# The optional closing run of '#' must be preceded by whitespace, otherwise a
# heading whose text legitimately ends in '#' (C#, F#) would lose it.
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_HR_RE = re.compile(r"^\s*(?:-\s*){3,}$|^\s*(?:\*\s*){3,}$|^\s*(?:_\s*){3,}$")
_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
_LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_TASK_RE = re.compile(r"^\[([ xX])\]\s*(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
_INDENT_CODE_RE = re.compile(r"^ {4,}\S")


def _is_new_block_start(line, next_line=None):
    """Report whether a line ends the paragraph currently being collected."""
    if not line.strip():
        return True
    if (_FENCE_RE.match(line) or _HEADING_RE.match(line) or _HR_RE.match(line)
            or _QUOTE_RE.match(line) or _LIST_RE.match(line)):
        return True
    return next_line is not None and "|" in line and bool(_TABLE_SEP_RE.match(next_line))


def parse_blocks(lines: List[str], top_level: bool = True) -> list:
    """Parse prepared lines into a flat list of block objects.

    `top_level` is False when parsing the inside of a blockquote, where a
    leading '---' is a horizontal rule rather than the start of frontmatter.
    """
    blocks = []
    i, n = 0, len(lines)
    if top_level and i < n and lines[i].strip() == "---":
        frontmatter, j = _try_frontmatter(lines, i)
        if frontmatter is not None:
            blocks.append(frontmatter)
            i = j
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        # Order matters: a horizontal rule is checked before a list, because
        # "- - -" matches both patterns and CommonMark resolves the ambiguity
        # in favour of the rule.
        match = _FENCE_RE.match(line)
        if match:
            block, i = _parse_code_block(lines, i, match)
            blocks.append(block)
            continue
        match = _HEADING_RE.match(line)
        if match:
            blocks.append(Heading(len(match.group(1)), match.group(2) or ""))
            i += 1
            continue
        if _HR_RE.match(line):
            blocks.append(Rule())
            i += 1
            continue
        if _QUOTE_RE.match(line):
            block, i = _parse_quote(lines, i)
            blocks.append(block)
            continue
        if _LIST_RE.match(line):
            block, i = _parse_list(lines, i)
            blocks.append(block)
            continue
        # Reaching this point means a block is genuinely starting here (the
        # paragraph parser consumes its own indented continuation lines), so a
        # four-space indent really is an indented code block.
        if _INDENT_CODE_RE.match(line):
            block, i = _parse_indented_code(lines, i)
            blocks.append(block)
            continue
        if "|" in line and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            block, i = _parse_table(lines, i)
            blocks.append(block)
            continue
        block, i = _parse_para(lines, i)
        blocks.append(block)
    return blocks


def _try_frontmatter(lines, i):
    """Parse a leading '---' block of flat key:value pairs, or give up cleanly."""
    j, pairs = i + 1, []
    while j < len(lines) and lines[j].strip() != "---":
        line = lines[j]
        if j - i > MAX_FRONTMATTER_LINES:
            return None, i  # too long to be a header; treat as ordinary content
        if not line.strip() or line.lstrip().startswith("#"):
            j += 1  # blank lines and comments are allowed inside the block
            continue
        if ":" not in line:
            return None, i  # doesn't look like flat frontmatter; bail out
        key, _, value = line.partition(":")
        pairs.append((key.strip(), value.strip()))
        j += 1
    if j >= len(lines):
        return None, i  # no closing fence found
    return FrontMatter(pairs), j + 1


def _parse_code_block(lines, i, match):
    """Parse a fenced code block, removing the fence's own indentation."""
    indent, fence, lang = len(match.group(1)), match.group(2), match.group(3)
    fence_char, fence_len = fence[0], len(fence)
    # The closing fence must use the SAME character with at least the opening
    # run length; that lets a shorter fence (``` inside a ```` block) act as
    # literal content instead of prematurely closing the outer block.
    close_re = re.compile(r"^\s*" + re.escape(fence_char) + "{" + str(fence_len) + r",}\s*$")
    body, j = [], i + 1
    while j < len(lines) and not close_re.match(lines[j]):
        body.append(_dedent(lines[j], indent))
        j += 1
    return CodeBlock(lang, body), (j + 1 if j < len(lines) else j)


def _parse_indented_code(lines, i):
    """Parse a run of four-space-indented lines into a code block."""
    body, j = [], i
    while j < len(lines):
        if lines[j].startswith(" " * 4):
            body.append(lines[j][4:])
            j += 1
        elif not lines[j].strip():
            # Blank lines only stay in the block if indented code follows them.
            k = j
            while k < len(lines) and not lines[k].strip():
                k += 1
            if k < len(lines) and lines[k].startswith(" " * 4):
                body.extend([""] * (k - j))
                j = k
            else:
                break
        else:
            break
    return CodeBlock("", body), j


def _dedent(line, amount):
    """Remove up to `amount` leading spaces from a line."""
    stripped = 0
    while stripped < amount and line[stripped:stripped + 1] == " ":
        stripped += 1
    return line[stripped:]


def _parse_quote(lines, i):
    """Collect consecutive '>' lines and parse their contents as nested blocks."""
    body, j = [], i
    while j < len(lines):
        match = _QUOTE_RE.match(lines[j])
        if not match:
            break
        body.append(match.group(1))
        j += 1
    return Quote(parse_blocks(body, top_level=False)), j


def _parse_list(lines, i):
    """Parse a run of list items, including any blocks nested under them."""
    items, indent_stack, counters = [], [], {}
    j = i
    while j < len(lines):
        line = lines[j]
        if not line.strip():
            # A blank line only ends the list if what follows is neither
            # another item nor indented content belonging to the last item.
            k = j
            while k < len(lines) and not lines[k].strip():
                k += 1
            if k < len(lines) and (_LIST_RE.match(lines[k]) or lines[k].startswith(" ")):
                j = k
                continue
            break
        match = _LIST_RE.match(line)
        if not match:
            if items and line.startswith(" "):
                j = _absorb_continuation(lines, j, items[-1])
                continue
            break
        indent, marker, rest = match.groups()
        width = len(indent)
        # Indentation stack: normalises arbitrary indent widths into clean
        # nesting levels, the same technique Python's own lexer uses.
        while indent_stack and indent_stack[-1] > width:
            indent_stack.pop()
        if not indent_stack or indent_stack[-1] < width:
            indent_stack.append(width)
        level = len(indent_stack) - 1
        # Leaving a nesting level discards its counter, so the next sublist
        # started at that level begins again at 1 rather than continuing.
        for deeper in [lvl for lvl in counters if lvl > level]:
            del counters[deeper]
        ordered = marker[0].isdigit()
        checked = None
        task = _TASK_RE.match(rest)
        if task:
            checked, rest = task.group(1).lower() == "x", task.group(2)
        number = _next_number(counters, level, marker) if ordered else 0
        items.append(ListItem(rest, level, ordered, number, checked))
        j += 1
    return ListBlock(items), j


def _next_number(counters, level, marker):
    """Return the display number for an ordered item at a given nesting level."""
    if level not in counters:
        # The list pattern guarantees the marker is digits followed by '.' or
        # ')', so this always parses. An explicit start value is honoured.
        counters[level] = int(marker.rstrip(".)"))
    else:
        counters[level] += 1
    return counters[level]


def _absorb_continuation(lines, j, item):
    """Attach an indented line that follows a list item, and return the next index.

    A fenced code block becomes a child block of the item so it keeps its
    formatting; anything else is either a wrapped continuation of the item's
    own text or, once children exist, a paragraph underneath them.
    """
    fence = _FENCE_RE.match(lines[j])
    if fence:
        block, j = _parse_code_block(lines, j, fence)
        item.children.append(block)
        return j
    text = lines[j].strip()
    if not item.children:
        item.text += " " + text
    elif isinstance(item.children[-1], Para):
        item.children[-1].text += " " + text
    else:
        item.children.append(Para(text))
    return j + 1


# Placeholder for a pipe character that must not act as a cell separator.
# Input sanitisation guarantees NUL cannot appear in a document, so it is
# always safe to borrow as a marker here.
_PIPE_MASK = "\x00"


def _split_row(line):
    """Split a table row into cells, ignoring escaped and code-span pipes."""
    line = line.strip()
    masked = _CODE_SPAN_RE.sub(lambda m: m.group(0).replace("|", _PIPE_MASK), line)
    masked = masked.replace("\\|", _PIPE_MASK)
    if masked.startswith("|"):
        masked = masked[1:]
    if masked.endswith("|"):
        masked = masked[:-1]
    return [cell.strip().replace(_PIPE_MASK, "|") for cell in masked.split("|")]


def _parse_table(lines, i):
    """Parse a pipe table: a header row, an alignment row, then body rows."""
    header = _split_row(lines[i])
    aligns = []
    for cell in _split_row(lines[i + 1]):
        left, right = cell.startswith(":"), cell.endswith(":")
        aligns.append("c" if left and right else "r" if right else "l")
    rows, j = [], i + 2
    while j < len(lines) and lines[j].strip() and "|" in lines[j]:
        rows.append(_split_row(lines[j]))
        j += 1
    return Table(header, aligns, rows), j


def _parse_para(lines, i):
    """Collect consecutive lines into one paragraph, reflowed at render time."""
    text_lines, j = [lines[i].strip()], i + 1
    while j < len(lines):
        following = lines[j + 1] if j + 1 < len(lines) else None
        if _is_new_block_start(lines[j], following):
            break
        text_lines.append(lines[j].strip())
        j += 1
    return Para(" ".join(text_lines)), j


# ============================================================
# Inline renderer (bold/italic/code/links/etc within a block's text)
# ============================================================
_CODE_SPAN_RE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.DOTALL)
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_AUTOLINK_RE = re.compile(r"<((?:https?|ftp)://[^>\s]+)>")


def _emph_re(char, count, word_guard):
    """Build the regular expression for one emphasis marker, e.g. '**'."""
    delim = re.escape(char * count)
    # Asterisk/underscore emphasis is guarded against word characters on
    # either side so it doesn't misfire inside snake_case identifiers or
    # arithmetic like 2**10 -- a deliberate simplification versus full
    # CommonMark, which does allow mid-word asterisk emphasis.
    guard = r"(?<!\w)" if word_guard else r"(?<!" + re.escape(char) + r")"
    guard_end = r"(?!\w)" if word_guard else r"(?!" + re.escape(char) + r")"
    return re.compile(guard + delim + r"(\S(?:.*?\S)?|\S)" + delim + guard_end, re.DOTALL)


_BOLD_ITALIC_STAR = _emph_re("*", 3, True)
_BOLD_ITALIC_U = _emph_re("_", 3, True)
_BOLD_STAR = _emph_re("*", 2, True)
_BOLD_U = _emph_re("_", 2, True)
_ITALIC_STAR = _emph_re("*", 1, True)
_ITALIC_U = _emph_re("_", 1, True)
_STRIKE_RE = re.compile(r"~~(\S(?:.*?\S)?|\S)~~", re.DOTALL)
_HIGHLIGHT_RE = re.compile(r"==(\S(?:.*?\S)?|\S)==", re.DOTALL)

_UNBOLD = sgr(SGR["unbold"])
_UNITALIC = sgr(SGR["unitalic"])
_UNBOLDITALIC = sgr(SGR["unbold"], SGR["unitalic"])
_UNSTRIKE = sgr(SGR["unstrike"])
_UNHIGHLIGHT = sgr(SGR["fg_default"], SGR["bg_default"])
_UNLINK = sgr(SGR["ununderline"], SGR["fg_default"])
_UNCODE = sgr(SGR["fg_default"], SGR["bg_default"])
_UNCOLOR = sgr(SGR["fg_default"])


def render_inline(text, theme):
    """Convert one block's Markdown text into a styled single-line string."""
    text, spans = _protect_code_spans(text)
    text = _IMAGE_RE.sub(
        lambda m: theme["link_url"] + f"[image: {m.group(1) or 'image'}]" + _UNCOLOR, text)
    text = _AUTOLINK_RE.sub(lambda m: theme["link_text"] + m.group(1) + _UNLINK, text)
    text = _LINK_RE.sub(lambda m: _render_link(m, theme), text)
    text = _render_emphasis(text, theme)
    return _restore_code_spans(text, spans, theme)


def _protect_code_spans(text):
    """Replace `code spans` with placeholders so emphasis can't alter them."""
    spans = []

    def stash(match):
        content = match.group(2)
        if content.startswith(" ") and content.endswith(" ") and content.strip():
            content = content[1:-1]  # CommonMark: trim one space each side
        spans.append(content)
        return f"\x00{len(spans) - 1}\x00"

    return _CODE_SPAN_RE.sub(stash, text), spans


def _restore_code_spans(text, spans, theme):
    """Put the stashed code spans back, wrapped in the inline-code style."""
    return re.sub(r"\x00(\d+)\x00",
                  lambda m: theme["code_inline"] + spans[int(m.group(1))] + _UNCODE, text)


def _render_link(match, theme):
    """Render [label](url) as an underlined label followed by the URL."""
    label = _render_emphasis(match.group(1), theme)
    # Both spans close with attribute-specific unsets rather than a full
    # reset, so a link inside bold text leaves the bold intact afterwards.
    return (theme["link_text"] + label + _UNLINK + " "
            + theme["link_url"] + "(" + match.group(2) + ")" + _UNCOLOR)


def _render_emphasis(text, theme):
    """Apply bold, italic, strikethrough and highlight styling, innermost last."""
    both = sgr(SGR["bold"], SGR["italic"])
    text = _BOLD_ITALIC_STAR.sub(lambda m: both + _render_emphasis(m.group(1), theme) + _UNBOLDITALIC, text)
    text = _BOLD_ITALIC_U.sub(lambda m: both + _render_emphasis(m.group(1), theme) + _UNBOLDITALIC, text)
    text = _BOLD_STAR.sub(lambda m: theme["bold"] + _render_emphasis(m.group(1), theme) + _UNBOLD, text)
    text = _BOLD_U.sub(lambda m: theme["bold"] + _render_emphasis(m.group(1), theme) + _UNBOLD, text)
    text = _ITALIC_STAR.sub(lambda m: theme["italic"] + _render_emphasis(m.group(1), theme) + _UNITALIC, text)
    text = _ITALIC_U.sub(lambda m: theme["italic"] + _render_emphasis(m.group(1), theme) + _UNITALIC, text)
    text = _STRIKE_RE.sub(lambda m: theme["strike"] + _render_emphasis(m.group(1), theme) + _UNSTRIKE, text)
    text = _HIGHLIGHT_RE.sub(lambda m: theme["highlight"] + _render_emphasis(m.group(1), theme) + _UNHIGHLIGHT, text)
    return text


# ============================================================
# Wrapping
# ============================================================
def _track_background(text, current):
    """Return the background colour still in effect at the end of `text`.

    The SGR parameters are walked in order because extended colours span
    several of them: '48;5;236' is one background instruction made of three
    parameters, and skipping over it correctly is what stops the scanner from
    mistaking the '5' or the '236' for an unrelated attribute.
    """
    for match in _ANSI_RE.finditer(text):
        params = match.group(0)[2:-1]
        parts = params.split(";") if params else ["0"]
        i = 0
        while i < len(parts):
            param = parts[i] or "0"
            if param in ("0", "49"):
                current = None
            elif param in ("38", "48"):
                mode = parts[i + 1] if i + 1 < len(parts) else ""
                span = 3 if mode == "5" else 5 if mode == "2" else 1
                if param == "48":
                    current = ";".join(parts[i:i + span])
                i += span
                continue
            elif param.isdigit() and (40 <= int(param) <= 47 or 100 <= int(param) <= 107):
                current = param
            i += 1
    return current


def _reflow_backgrounds(lines):
    """Close any open background at each line end and reopen it on the next.

    Foreground attributes survive a newline harmlessly, but a background left
    open paints the rest of the terminal row, so a wrapped inline code span
    would otherwise smear a shaded bar out to the right edge.
    """
    background, out = None, []
    for line in lines:
        opened = sgr(background) if background else ""
        background = _track_background(line, background)
        closed = sgr(SGR["bg_default"]) if background else ""
        out.append(opened + line + closed)
    return out


def _wrap_styled(styled, width):
    """Word-wrap styled text to `width` columns, measuring visible width only.

    Words longer than the whole line (a long URL, say) are broken rather than
    allowed to overflow, and styling is repaired across every wrap point.
    """
    width = max(width, 1)
    lines, current, current_len = [], [], 0
    for word in styled.split(" "):
        word_len = _visible_len(word)
        if word_len > width:
            if current:
                lines.append(" ".join(current))
                current, current_len = [], 0
            chunks = _chunk_styled(word, width)
            lines.extend(chunks[:-1])
            current, current_len = [chunks[-1]], _visible_len(chunks[-1])
            continue
        needed = word_len + (1 if current else 0)
        if current and current_len + needed > width:
            lines.append(" ".join(current))
            current, current_len = [word], word_len
        else:
            current.append(word)
            current_len += needed
    if current:
        lines.append(" ".join(current))
    return _reflow_backgrounds(lines or [""])


# ============================================================
# Syntax highlighting (bash / python only -- everything else is left plain)
# ============================================================
_PY_KEYWORDS = {"def", "class", "return", "if", "elif", "else", "for", "while", "import", "from",
    "as", "with", "try", "except", "finally", "raise", "pass", "break", "continue", "lambda",
    "yield", "None", "True", "False", "and", "or", "not", "in", "is", "async", "await", "global",
    "nonlocal", "del", "assert"}
_SH_KEYWORDS = {"if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done", "case",
    "esac", "function", "return", "local", "export", "readonly", "declare", "set", "unset",
    "shift", "break", "continue", "exit", "in", "select", "time"}
_STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
_PY_KEYWORD_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, _PY_KEYWORDS))) + r")\b")
_SH_KEYWORD_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, _SH_KEYWORDS))) + r")\b")
_UNKW = sgr(SGR["unbold"], SGR["fg_default"])
_STRING_MASK = "\x01"


def _highlight_code_line(line, lang, theme):
    """Colour keywords, strings and comments in one line of bash or Python."""
    if lang not in ("python", "py", "bash", "sh", "shell"):
        return line
    keyword_re = _PY_KEYWORD_RE if lang in ("python", "py") else _SH_KEYWORD_RE
    strings = []

    def stash(match):
        strings.append(match.group(0))
        return f"{_STRING_MASK}{len(strings) - 1}{_STRING_MASK}"

    line = _STRING_RE.sub(stash, line)
    # Split on the first unprotected '#'. Protecting strings first means a
    # '#' inside a real string literal is never mistaken for a comment start.
    code_part, separator, comment_part = line.partition("#")
    comment_part = "#" + comment_part if separator else ""
    code_part = keyword_re.sub(lambda m: theme["code_keyword"] + m.group(1) + _UNKW, code_part)
    code_part = re.sub(_STRING_MASK + r"(\d+)" + _STRING_MASK,
                       lambda m: theme["code_string"] + strings[int(m.group(1))] + _UNCOLOR,
                       code_part)
    if comment_part:
        # A stashed string can sit inside what turned out to be a comment;
        # restore it as plain text so the comment reads as one uniform colour.
        comment_part = re.sub(_STRING_MASK + r"(\d+)" + _STRING_MASK,
                              lambda m: strings[int(m.group(1))], comment_part)
        comment_part = theme["code_comment"] + comment_part + sgr(SGR["undim"], SGR["fg_default"])
    return code_part + comment_part


# ============================================================
# Block renderers
# ============================================================
def render_blocks(blocks, theme, width):
    """Render a list of blocks into terminal lines, one blank line between each."""
    width = max(width, MIN_BODY_WIDTH)
    out = []
    for block in blocks:
        if isinstance(block, Heading):
            out.extend(_render_heading(block, theme, width))
        elif isinstance(block, Para):
            out.extend(_wrap_styled(render_inline(block.text, theme), width))
        elif isinstance(block, ListBlock):
            out.extend(_render_list(block, theme, width))
        elif isinstance(block, CodeBlock):
            out.extend(_render_code(block, theme, width))
        elif isinstance(block, Quote):
            out.extend(_render_quote(block, theme, width))
        elif isinstance(block, Table):
            out.extend(_render_table(block, theme, width))
        elif isinstance(block, Rule):
            out.append(theme["rule"] + (theme["g_hbar"] * width) + RESET)
        elif isinstance(block, FrontMatter):
            out.extend(_render_frontmatter(block, theme, width))
        out.append("")
    return out


def _trim_trailing_blanks(lines):
    """Drop blank lines from the end of a rendered block list."""
    while lines and not _strip_ansi(lines[-1]).strip():
        lines.pop()
    return lines


def _render_heading(block, theme, width):
    """Render a heading, underlining level-1 headings with a full-width rule."""
    style = theme["h1"] if block.level == 1 else theme["h2"] if block.level == 2 else theme["h_other"]
    # The style is reapplied per line rather than left open across the wrap,
    # so each rendered line is self-contained.
    lines = [style + line + RESET
             for line in _wrap_styled(render_inline(block.text, theme), width)]
    if block.level == 1:
        lines.append(theme["rule"] + (theme["g_hbar"] * width) + RESET)
    return lines


def _render_quote(block, theme, width):
    """Render a blockquote as a vertical bar beside its contents."""
    inner_width = max(width - 2, MIN_BODY_WIDTH)
    bar = theme["quote_bar"] + theme["g_quote"] + " "
    # The common case -- a quote holding a single paragraph -- keeps the
    # italic quote styling. Anything richer (a list, a code sample, several
    # paragraphs) is rendered normally so its structure survives.
    if len(block.blocks) == 1 and isinstance(block.blocks[0], Para):
        body = _wrap_styled(render_inline(block.blocks[0].text, theme), inner_width)
        return [bar + theme["quote"] + line + RESET for line in body]
    body = _trim_trailing_blanks(render_blocks(block.blocks, theme, inner_width))
    return [bar + _UNCOLOR + line + RESET for line in body]


def _labelled_lines(key, value, width, key_style, value_style):
    """Render one 'key: value' pair, wrapping the value under a hanging indent."""
    label = key_style + key + sgr(SGR["unbold"], SGR["fg_default"]) + ": "
    indent = _display_width(key) + 2
    if indent + MIN_BODY_WIDTH > width:
        # A key that long leaves no room for a hanging indent, so the pair is
        # wrapped as one continuous run of text instead.
        return [line + RESET for line in _wrap_styled(label + value_style + value, width)]
    body = _wrap_styled(value, width - indent)
    lines = [label + value_style + body[0] + RESET]
    lines.extend(" " * indent + value_style + line + RESET for line in body[1:])
    return lines


def _render_frontmatter(block, theme, width):
    """Render frontmatter as a key/value panel between two rules."""
    rule = theme["rule"] + (theme["g_hbar"] * width) + RESET
    lines = [rule]
    for key, value in block.pairs:
        lines.extend(_labelled_lines(key, value, width, theme["fm_key"], theme["fm_value"]))
    lines.append(rule)
    return lines


def _list_marker(item, theme):
    """Return the styled marker that introduces one list item."""
    if item.checked is not None:
        return (theme["g_checked"] if item.checked else theme["g_unchecked"]) + " "
    if item.ordered:
        return f"{item.number}. "
    glyph = theme["g_bullet"] if item.level % 2 == 0 else theme["g_circle"]
    return theme["bullet"] + glyph + _UNCOLOR + " "


def _render_list(block, theme, width):
    """Render list items with hanging indents and any blocks nested under them."""
    lines = []
    for item in block.items:
        prefix = "  " * item.level + _list_marker(item, theme)
        prefix_len = _visible_len(prefix)
        pad = " " * prefix_len
        body = _wrap_styled(render_inline(item.text, theme), max(width - prefix_len, MIN_BODY_WIDTH))
        lines.append(prefix + body[0])
        lines.extend(pad + line for line in body[1:])
        if item.children:
            child_width = max(width - prefix_len, MIN_BODY_WIDTH)
            child_lines = _trim_trailing_blanks(render_blocks(item.children, theme, child_width))
            lines.extend(pad + line for line in child_lines)
    return lines


def _render_code(block, theme, width):
    """Render a code block as a shaded panel with line numbers.

    Lines too long for the panel are wrapped onto continuation rows rather
    than allowed to spill past the right edge, which would break the shading.
    """
    background = theme["code_bg"]
    num_width = len(str(len(block.lines))) if block.lines else 1
    gutter = num_width + 3  # line number, a space, the separator, a space
    body_width = max(width - gutter, MIN_BODY_WIDTH)
    blank = sgr(background) + (" " * width) + sgr(SGR["bg_default"])
    lines = [blank]
    for index, source_line in enumerate(block.lines, 1):
        for part, chunk in enumerate(_chunk_styled(source_line, body_width)):
            highlighted = _highlight_code_line(chunk, block.lang.lower(), theme)
            label = str(index).rjust(num_width) if part == 0 else " " * num_width
            separator = theme["g_vbar"] if part == 0 else theme["g_cont"]
            pad = " " * max(width - gutter - _visible_len(highlighted), 0)
            lines.append(sgr(background) + theme["code_lineno"] + label + _UNCOLOR
                         + " " + separator + " " + highlighted + pad + sgr(SGR["bg_default"]))
    lines.append(blank)
    return lines


def _table_col_widths(header, rows, total_width, ncols):
    """Choose column widths that fit the table into the available width."""
    natural = [_visible_len(header[c]) if c < len(header) else 0 for c in range(ncols)]
    for row in rows:
        for c in range(ncols):
            if c < len(row):
                natural[c] = max(natural[c], _visible_len(row[c]))
    budget = total_width - (ncols + 1) - ncols * 2  # borders plus one space each side
    if budget < ncols:
        return [1] * ncols  # nothing sensible fits; degrade rather than fail
    if sum(natural) <= budget:
        return [max(w, 3) for w in natural]
    # Shrink proportionally, but never below a floor that still fits the
    # budget -- a fixed floor is what used to let wide tables overflow.
    floor = max(1, min(6, budget // ncols))
    scale = budget / sum(natural)
    widths = [max(int(w * scale), floor) for w in natural]
    while sum(widths) > budget:
        widest = widths.index(max(widths))
        if widths[widest] <= floor:
            break
        widths[widest] -= 1
    return widths


def _table_border(col_width, left, mid, right, theme):
    """Draw one horizontal table border using the theme's corner glyphs."""
    segments = [theme["g_hbar"] * (w + 2) for w in col_width]
    return theme["table_border"] + left + mid.join(segments) + right + RESET


def _table_row(cells, col_width, aligns, theme, is_header):
    """Render one table row, wrapping cell contents and applying alignment."""
    wrapped = [_wrap_styled(cells[c], col_width[c]) if c < len(cells) else [""]
               for c in range(len(col_width))]
    height = max((len(w) for w in wrapped), default=1)
    border = theme["table_border"]
    edge = border + theme["g_vbar"] + _UNCOLOR
    lines = []
    for row in range(height):
        parts = [edge]
        for c in range(len(col_width)):
            text = wrapped[c][row] if row < len(wrapped[c]) else ""
            if is_header:
                text = theme["table_header"] + text + _UNBOLD
            pad = max(col_width[c] - _visible_len(text), 0)
            align = aligns[c] if c < len(aligns) else "l"
            if align == "r":
                cell = " " * pad + text
            elif align == "c":
                left = pad // 2
                cell = " " * left + text + " " * (pad - left)
            else:
                cell = text + " " * pad
            parts.append(" " + cell + " " + edge)
        lines.append("".join(parts) + RESET)
    return lines


def _render_table_records(header, rows, theme, width):
    """List a table's cells as labelled records, for terminals too narrow for a grid."""
    if not rows:
        return [line for cell in header for line in _wrap_styled(cell, width)]
    lines = []
    for index, row in enumerate(rows):
        if index:
            lines.append("")  # blank line between records keeps them readable
        for column, cell in enumerate(row):
            key = _strip_ansi(header[column]).strip() if column < len(header) else ""
            key = key or f"column {column + 1}"
            lines.extend(_labelled_lines(key, cell, width, theme["table_header"], _UNCOLOR))
    return lines


def _render_table(block, theme, width):
    """Render a pipe table with box-drawing borders and per-column alignment."""
    # Column count follows the widest row, so a row with extra cells keeps
    # its data instead of having the surplus silently dropped.
    ncols = max([1, len(block.header)] + [len(r) for r in block.rows])
    header = (block.header + [""] * ncols)[:ncols]
    aligns = (block.aligns + ["l"] * ncols)[:ncols]
    styled_header = [render_inline(c, theme) for c in header]
    styled_rows = [[render_inline(c, theme) for c in row] for row in block.rows]
    # Drawing a grid needs one border per gap, a space of padding each side of
    # every cell, and at least one column of content per cell. When even that
    # does not fit, a grid can only overflow, so the data is listed instead.
    if 4 * ncols + 1 > width:
        return _render_table_records(styled_header, styled_rows, theme, width)
    col_width = _table_col_widths(styled_header, styled_rows, width, ncols)
    lines = [_table_border(col_width, theme["g_tl"], theme["g_tm"], theme["g_tr"], theme)]
    lines.extend(_table_row(styled_header, col_width, aligns, theme, True))
    lines.append(_table_border(col_width, theme["g_ml"], theme["g_mm"], theme["g_mr"], theme))
    for row in styled_rows:
        lines.extend(_table_row(row, col_width, aligns, theme, False))
    lines.append(_table_border(col_width, theme["g_bl"], theme["g_bm"], theme["g_br"], theme))
    return lines


# ============================================================
# TOC / section extraction
# ============================================================
def render_toc(blocks, theme):
    """Render the document's headings as an indented outline."""
    lines = [theme["bullet"] + "  " * (b.level - 1) + theme["g_bullet"] + " " + _UNCOLOR
             + render_inline(b.text, theme) + RESET
             for b in blocks if isinstance(b, Heading)]
    return lines or ["(no headings found)"]


def extract_section(blocks, name):
    """Return the blocks of one section, or None if no heading matches `name`.

    An exact heading match wins; failing that, the first heading containing
    the name is used, so a partial title is enough to jump to a section.
    """
    target = name.strip().lower()
    headings = [(i, b) for i, b in enumerate(blocks) if isinstance(b, Heading)]
    start = next((i for i, b in headings if b.text.strip().lower() == target), None)
    if start is None:
        start = next((i for i, b in headings if target in b.text.strip().lower()), None)
    if start is None:
        return None
    level = blocks[start].level
    end = next((i for i, b in headings if i > start and b.level <= level), len(blocks))
    return blocks[start:end]


# ============================================================
# Terminal / environment handling
# ============================================================
def _isatty(stream):
    """Report whether a stream is an interactive terminal, tolerating odd streams."""
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def _supports_unicode():
    """Report whether stdout's encoding can represent the box-drawing glyphs."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        "".join(GLYPHS_UNICODE.values()).encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError, TypeError):
        return False


def _configure_stdout():
    """Make stdout replace characters it cannot encode instead of raising.

    Under a C/POSIX locale the output encoding is ASCII. Without this the
    first box-drawing character would abort the whole program.
    """
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError, OSError):
        pass  # not a real file stream (e.g. captured during tests)


def _discard_stdout():
    """Point stdout at the null device after a broken pipe.

    Without this, the interpreter's own flush at exit hits the closed pipe
    again and prints an 'Exception ignored' message the user cannot suppress.
    """
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError, AttributeError):
        pass


def _write_out(text):
    """Write rendered text to stdout, tolerating a reader that closed the pipe."""
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except BrokenPipeError:
        _discard_stdout()
    except OSError as exc:
        if exc.errno != errno.EPIPE:
            raise
        _discard_stdout()


def _warn(message):
    """Print a diagnostic to stderr in the tool's standard prefixed form."""
    print(f"mdcat: {message}", file=sys.stderr)


def _resolve_color(no_color_flag):
    """Decide whether to emit colour, honouring NO_COLOR and FORCE_COLOR."""
    if no_color_flag or os.environ.get("NO_COLOR", ""):
        return False
    if os.environ.get("FORCE_COLOR", ""):
        return True
    return _isatty(sys.stdout)


def _load_config():
    """Read the optional key=value config file; missing or unreadable files are ignored."""
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    path = os.path.join(config_home, "mdcat", "config")
    config = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                config[key.strip()] = value.strip()
    except OSError:
        pass  # no config, unreadable, or replaced by a directory -- use defaults
    return config


def _config_width(config):
    """Return the configured width, warning if the value is unusable."""
    raw = config.get("width")
    if raw is None:
        return None
    if raw.isdigit() and int(raw) >= MIN_WIDTH:
        return int(raw)
    _warn(f"ignoring invalid config width {raw!r} (expected an integer >= {MIN_WIDTH})")
    return None


def _config_theme(config):
    """Return the configured theme name, warning if it is not a known theme."""
    raw = config.get("theme")
    if raw is None:
        return None
    if raw in THEMES:
        return raw
    _warn(f"ignoring unknown config theme {raw!r} (expected one of: {', '.join(sorted(THEMES))})")
    return None


def _file_header(name, theme, width):
    """Draw a centred banner naming one file, used when several are rendered."""
    label = f" {name} "
    pad = max(width - _visible_len(label), 0)
    left = pad // 2
    return (theme["rule"] + (theme["g_dbar"] * left) + sgr(SGR["bold"]) + label + _UNBOLD
            + theme["rule"] + (theme["g_dbar"] * (pad - left)) + RESET)


# ============================================================
# CLI / main
# ============================================================
def build_arg_parser():
    """Construct the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="mdcat", description="Render a Markdown file to the terminal.")
    parser.add_argument("files", nargs="*",
                        help="Markdown file(s) to render. Reads stdin if omitted or '-'.")
    parser.add_argument("--width", type=int, metavar="N",
                        help=f"Wrap width in columns, minimum {MIN_WIDTH} "
                             "(default: detect terminal width)")
    parser.add_argument("--theme", choices=sorted(THEMES),
                        help="Colour theme (default: dark, or the config file setting)")
    parser.add_argument("--no-color", "--plain", dest="no_color", action="store_true",
                        help="Disable all styling and print plain text")
    parser.add_argument("--ascii", action="store_true",
                        help="Draw with ASCII characters instead of box-drawing glyphs")
    parser.add_argument("--no-pager", action="store_true",
                        help="Don't page long output through $PAGER/less")
    # --toc and --heading both replace the normal full-document view, so
    # asking for both is a mistake worth reporting rather than resolving.
    view = parser.add_mutually_exclusive_group()
    view.add_argument("--toc", action="store_true", help="Print only the heading outline")
    view.add_argument("--heading", metavar="NAME",
                      help="Print only the section starting at this heading")
    parser.add_argument("--version", action="version", version=f"mdcat {VERSION}")
    return parser


def _page(text, config):
    """Display text through $PAGER; return False if no pager could be started."""
    command = os.environ.get("PAGER") or config.get("pager") or "less -R"
    try:
        argv = shlex.split(command)  # respects quoting, unlike a plain split
    except ValueError:
        _warn(f"ignoring malformed pager command {command!r}")
        return False
    if not argv:
        return False
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE)
    except OSError:
        return False  # pager not installed -- caller falls back to printing
    try:
        process.communicate(input=text.encode("utf-8", "replace"))
    except (OSError, KeyboardInterrupt):
        process.kill()
        process.wait()
    return True


def _emit(out_lines, use_color, no_pager_flag, config):
    """Send rendered lines to the pager when appropriate, otherwise to stdout."""
    if not out_lines:
        return  # nothing rendered; stay silent rather than print a blank line
    text = "\n".join(out_lines)
    if not use_color:
        text = _strip_ansi(text)
    height = shutil.get_terminal_size((DEFAULT_WIDTH, DEFAULT_HEIGHT)).lines
    should_page = (not no_pager_flag and config.get("pager") != "disabled"
                   and _isatty(sys.stdout) and len(out_lines) > height)
    if should_page and _page(text, config):
        return
    _write_out(text)


def _read_document(name):
    """Read one document by name, or standard input when the name is '-'."""
    if name == "-":
        return sys.stdin.read()
    with open(name, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _render_document(name, text, args, theme, width, multiple):
    """Render one document to lines; returns (lines, ok) where ok is False on error."""
    lines = []
    if name and multiple:
        lines.append(_file_header(name, theme, width))
    blocks = parse_blocks(_prepare_lines(text))
    if args.toc:
        lines.extend(render_toc(blocks, theme))
        lines.append("")
        return lines, True
    if args.heading:
        section = extract_section(blocks, args.heading)
        if section is None:
            _warn(f'no heading matching "{args.heading}" found in '
                  f'{name or "standard input"}')
            return lines, False
        blocks = section
    lines.extend(render_blocks(blocks, theme, width))
    return lines, True


def main(argv=None):
    """Entry point: parse arguments, render every input, and return an exit code."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.width is not None and args.width < MIN_WIDTH:
        parser.error(f"--width must be at least {MIN_WIDTH}")
    if not args.files and _isatty(sys.stdin):
        parser.print_help()  # no file and nothing piped in: show usage
        return 1

    _configure_stdout()
    config = _load_config()
    detected = shutil.get_terminal_size((DEFAULT_WIDTH, DEFAULT_HEIGHT)).columns
    width = max(args.width or _config_width(config) or detected, MIN_WIDTH)
    theme = build_theme(args.theme or _config_theme(config) or "dark",
                        ascii_only=args.ascii or not _supports_unicode())
    use_color = _resolve_color(args.no_color)

    targets = args.files or ["-"]
    out_lines, failed = [], False
    for name in targets:
        try:
            text = _read_document(name)
        except OSError as exc:
            _warn(f"{name}: {exc.strerror or exc}")
            failed = True
            continue
        lines, ok = _render_document(name if name != "-" else None, text, args,
                                     theme, width, len(targets) > 1)
        out_lines.extend(lines)
        failed = failed or not ok

    _emit(_trim_trailing_blanks(out_lines), use_color, args.no_pager, config)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)  # conventional exit code for interruption by Ctrl-C
