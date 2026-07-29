#!/usr/bin/env python3
"""mdcat - render a Markdown file to the terminal.

Zero dependencies (standard library only). Supports headers, emphasis,
inline code, fenced/indented code blocks (with light bash/Python
highlighting + line numbers), nested/ordered/task lists, blockquotes,
pipe tables, horizontal rules, links, images, ==highlight==,
~~strikethrough~~, and flat key:value frontmatter.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

VERSION = "0.1"

# ============================================================
# ANSI / theming
# ============================================================
# Attribute "unset" codes (22/23/24/29/39/49) are used instead of a blanket
# reset (0) when closing an inline span, so a nested style (e.g. a code
# span inside bold) doesn't clobber the *outer* style when it ends. A full
# reset is only used at the end of a whole rendered block/line as a safety net.
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
    return f"\x1b[{';'.join(codes)}m"


THEMES = {
    "dark": {
        "h1": sgr(SGR["bold"], "38;5;39"), "h2": sgr(SGR["bold"], "38;5;45"),
        "h_other": sgr(SGR["bold"], "38;5;51"),
        "bold": sgr(SGR["bold"]), "italic": sgr(SGR["italic"]),
        "strike": sgr(SGR["strike"]), "highlight": sgr("30", "48;5;227"),
        "code_inline": sgr("38;5;217", "48;5;236"), "code_bg": "48;5;236",
        "code_comment": sgr(SGR["dim"], "38;5;108"), "code_string": sgr("38;5;180"),
        "code_keyword": sgr(SGR["bold"], "38;5;75"), "code_lineno": sgr(SGR["dim"], "38;5;244"),
        "quote": sgr(SGR["dim"], SGR["italic"], "38;5;109"), "quote_bar": sgr("38;5;109"),
        "rule": sgr(SGR["dim"], "38;5;240"),
        "table_border": sgr("38;5;240"), "table_header": sgr(SGR["bold"]),
        "link_text": sgr(SGR["underline"], "38;5;75"), "link_url": sgr(SGR["dim"]),
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
        "code_keyword": sgr(SGR["bold"], "38;5;18"), "code_lineno": sgr(SGR["dim"], "38;5;242"),
        "quote": sgr(SGR["dim"], SGR["italic"], "38;5;24"), "quote_bar": sgr("38;5;24"),
        "rule": sgr(SGR["dim"], "38;5;250"),
        "table_border": sgr("38;5;250"), "table_header": sgr(SGR["bold"]),
        "link_text": sgr(SGR["underline"], "38;5;18"), "link_url": sgr(SGR["dim"]),
        "fm_key": sgr(SGR["bold"], "38;5;28"), "fm_value": sgr("38;5;238"),
        "bullet": sgr("38;5;18"),
    },
}

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s):
    return _ANSI_RE.sub("", s)


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


@dataclass
class ListBlock:
    items: List[ListItem] = field(default_factory=list)


@dataclass
class CodeBlock:
    lang: str
    lines: List[str] = field(default_factory=list)


@dataclass
class Quote:
    text: str


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
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})\s*(\S*)\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_HR_RE = re.compile(r"^\s*(?:-\s*){3,}$|^\s*(?:\*\s*){3,}$|^\s*(?:_\s*){3,}$")
_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
_LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_TASK_RE = re.compile(r"^\[([ xX])\]\s+(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")


def _is_new_block_start(line, next_line=None):
    if not line.strip():
        return True
    if (_FENCE_RE.match(line) or _HEADING_RE.match(line) or _HR_RE.match(line)
            or _QUOTE_RE.match(line) or _LIST_RE.match(line)):
        return True
    if next_line is not None and "|" in line and _TABLE_SEP_RE.match(next_line):
        return True
    return False


def parse_blocks(lines: List[str]) -> list:
    blocks = []
    i, n = 0, len(lines)
    if i < n and lines[i].strip() == "---":
        fm, j = _try_frontmatter(lines, i)
        if fm is not None:
            blocks.append(fm)
            i = j
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        # order matters: HR is checked before list, since e.g. "- - -" matches
        # both patterns and CommonMark resolves the ambiguity in favor of HR
        m = _FENCE_RE.match(line)
        if m:
            block, i = _parse_code_block(lines, i, m)
            blocks.append(block)
            continue
        m = _HEADING_RE.match(line)
        if m:
            blocks.append(Heading(len(m.group(1)), m.group(2)))
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
        if "|" in line and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            block, i = _parse_table(lines, i)
            blocks.append(block)
            continue
        block, i = _parse_para(lines, i)
        blocks.append(block)
    return blocks


def _try_frontmatter(lines, i):
    j, pairs = i + 1, []
    while j < len(lines) and lines[j].strip() != "---":
        line = lines[j]
        if ":" not in line or not line.strip() or j - i > 50:
            return None, i  # doesn't look like flat frontmatter; bail out
        key, _, val = line.partition(":")
        pairs.append((key.strip(), val.strip()))
        j += 1
    if j >= len(lines):
        return None, i  # no closing fence found
    return FrontMatter(pairs), j + 1


def _parse_code_block(lines, i, m):
    fence, lang = m.group(1), m.group(2)
    fence_char, fence_len = fence[0], len(fence)
    # the closing fence must be the SAME character with >= the opening run
    # length; this lets a shorter fence (e.g. ``` inside a ```` block) act as
    # literal content instead of prematurely closing the outer block
    close_re = re.compile(r"^\s*" + re.escape(fence_char) + "{" + str(fence_len) + r",}\s*$")
    body, j = [], i + 1
    while j < len(lines) and not close_re.match(lines[j]):
        body.append(lines[j])
        j += 1
    return CodeBlock(lang, body), (j + 1 if j < len(lines) else j)


def _parse_quote(lines, i):
    text_lines, j = [], i
    while j < len(lines):
        m = _QUOTE_RE.match(lines[j])
        if not m:
            break
        text_lines.append(m.group(1))
        j += 1
    return Quote(" ".join(text_lines)), j


def _parse_list(lines, i):
    items, indent_stack, counters = [], [], {}
    j = i
    while j < len(lines):
        line = lines[j]
        if not line.strip():
            if j + 1 < len(lines) and _LIST_RE.match(lines[j + 1]):
                j += 1
                continue
            break
        m = _LIST_RE.match(line)
        if not m:
            if items and line.startswith(" "):  # wrapped continuation of the last item
                items[-1].text += " " + line.strip()
                j += 1
                continue
            break
        indent, marker, rest = m.groups()
        width = len(indent)
        # indentation-stack: normalizes arbitrary indent widths into clean
        # nesting levels, the same technique Python's own lexer uses
        while indent_stack and indent_stack[-1] > width:
            indent_stack.pop()
        if not indent_stack or indent_stack[-1] < width:
            indent_stack.append(width)
        level = len(indent_stack) - 1
        ordered = marker[0].isdigit()
        checked = None
        tm = _TASK_RE.match(rest)
        if tm:
            checked, rest = tm.group(1).lower() == "x", tm.group(2)
        if ordered:
            if level not in counters:
                try:
                    counters[level] = int(marker.rstrip(".)"))
                except ValueError:
                    counters[level] = 1
            else:
                counters[level] += 1
            number = counters[level]
        else:
            number = 0
        items.append(ListItem(rest, level, ordered, number, checked))
        j += 1
    return ListBlock(items), j


def _split_row(line):
    line = line.strip()
    line = line[1:] if line.startswith("|") else line
    line = line[:-1] if line.endswith("|") else line
    return [c.strip() for c in re.split(r"(?<!\\)\|", line)]


def _parse_table(lines, i):
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
    text_lines, j = [lines[i].strip()], i + 1
    while j < len(lines):
        nxt = lines[j + 1] if j + 1 < len(lines) else None
        if _is_new_block_start(lines[j], nxt):
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


def _emph_re(ch, count, word_guard):
    d = re.escape(ch * count)
    # asterisk/underscore emphasis is guarded against word characters on
    # either side so it doesn't misfire inside snake_case identifiers or
    # arithmetic like 2**10 -- a deliberate simplification vs full CommonMark,
    # which does allow *mid-word* asterisk emphasis
    guard = r"(?<!\w)" if word_guard else r"(?<!" + re.escape(ch) + r")"
    guard_end = r"(?!\w)" if word_guard else r"(?!" + re.escape(ch) + r")"
    return re.compile(guard + d + r"(\S(?:.*?\S)?|\S)" + d + guard_end, re.DOTALL)


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


def render_inline(text, theme):
    text, spans = _protect_code_spans(text)
    text = _IMAGE_RE.sub(lambda m: theme["link_url"] + f"[image: {m.group(1) or 'image'}]" + RESET, text)
    text = _AUTOLINK_RE.sub(lambda m: theme["link_text"] + m.group(1) + _UNLINK, text)
    text = _LINK_RE.sub(lambda m: _render_link(m, theme), text)
    text = _render_emphasis(text, theme)
    text = _restore_code_spans(text, spans, theme)
    return text


def _protect_code_spans(text):
    spans = []

    def stash(m):
        content = m.group(2)
        if content.startswith(" ") and content.endswith(" ") and content.strip():
            content = content[1:-1]  # CommonMark: trim one space each side, unless it's all spaces
        spans.append(content)
        return f"\x00{len(spans) - 1}\x00"

    return _CODE_SPAN_RE.sub(stash, text), spans


def _restore_code_spans(text, spans, theme):
    return re.sub(r"\x00(\d+)\x00", lambda m: theme["code_inline"] + spans[int(m.group(1))] + _UNCODE, text)


def _render_link(m, theme):
    label = _render_emphasis(m.group(1), theme)
    return theme["link_text"] + label + _UNLINK + " " + theme["link_url"] + f"({m.group(2)})" + RESET


def _render_emphasis(text, theme):
    bi = sgr(SGR["bold"], SGR["italic"])
    text = _BOLD_ITALIC_STAR.sub(lambda m: bi + _render_emphasis(m.group(1), theme) + _UNBOLDITALIC, text)
    text = _BOLD_ITALIC_U.sub(lambda m: bi + _render_emphasis(m.group(1), theme) + _UNBOLDITALIC, text)
    text = _BOLD_STAR.sub(lambda m: theme["bold"] + _render_emphasis(m.group(1), theme) + _UNBOLD, text)
    text = _BOLD_U.sub(lambda m: theme["bold"] + _render_emphasis(m.group(1), theme) + _UNBOLD, text)
    text = _ITALIC_STAR.sub(lambda m: theme["italic"] + _render_emphasis(m.group(1), theme) + _UNITALIC, text)
    text = _ITALIC_U.sub(lambda m: theme["italic"] + _render_emphasis(m.group(1), theme) + _UNITALIC, text)
    text = _STRIKE_RE.sub(lambda m: theme["strike"] + _render_emphasis(m.group(1), theme) + _UNSTRIKE, text)
    text = _HIGHLIGHT_RE.sub(lambda m: theme["highlight"] + _render_emphasis(m.group(1), theme) + _UNHIGHLIGHT, text)
    return text


def _wrap_styled(styled, width):
    """Word-wrap text that may contain ANSI codes. Splits on literal spaces
    (never present inside an escape sequence) and packs words by *visible*
    length. No explicit re-opening of a style is needed across a wrap point:
    terminal styling naturally persists across a newline, so an unclosed
    code at the end of one line just continues correctly onto the next."""
    words = styled.split(" ")
    lines, cur, cur_len = [], [], 0
    for w in words:
        wlen = len(_strip_ansi(w))
        needed = wlen + (1 if cur else 0)
        if cur and cur_len + needed > width:
            lines.append(" ".join(cur))
            cur, cur_len = [w], wlen
        else:
            cur.append(w)
            cur_len += needed
    if cur:
        lines.append(" ".join(cur))
    return lines or [""]


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
_UNKW = sgr(SGR["unbold"], SGR["fg_default"])


def _highlight_code_line(line, lang, theme):
    if lang not in ("python", "py", "bash", "sh", "shell"):
        return line
    keywords = _PY_KEYWORDS if lang in ("python", "py") else _SH_KEYWORDS
    strings = []

    def stash(m):
        strings.append(m.group(0))
        return f"\x01{len(strings) - 1}\x01"

    line = _STRING_RE.sub(stash, line)
    # split on the first unprotected '#' -- protecting strings first means a
    # '#' inside a real string literal is never mistaken for a comment start
    code_part, sep, comment_part = line.partition("#")
    comment_part = "#" + comment_part if sep else ""
    kw_re = re.compile(r"\b(" + "|".join(re.escape(k) for k in keywords) + r")\b")
    code_part = kw_re.sub(lambda m: theme["code_keyword"] + m.group(1) + _UNKW, code_part)
    code_part = re.sub(r"\x01(\d+)\x01", lambda m: theme["code_string"] + strings[int(m.group(1))] + sgr(SGR["fg_default"]), code_part)
    if comment_part:
        # a '#' that the regex matched *inside* what's actually a comment can
        # still contain a stashed placeholder; restore it as plain text here
        # (no string color) so the whole comment reads as one uniform color
        comment_part = re.sub(r"\x01(\d+)\x01", lambda m: strings[int(m.group(1))], comment_part)
        comment_part = theme["code_comment"] + comment_part + sgr(SGR["undim"], SGR["fg_default"])
    return code_part + comment_part


# ============================================================
# Block renderers
# ============================================================
def render_blocks(blocks, theme, width):
    out = []
    for b in blocks:
        if isinstance(b, Heading):
            out.extend(_render_heading(b, theme, width))
        elif isinstance(b, Para):
            out.extend(_wrap_styled(render_inline(b.text, theme), width))
        elif isinstance(b, ListBlock):
            out.extend(_render_list(b, theme, width))
        elif isinstance(b, CodeBlock):
            out.extend(_render_code(b, theme, width))
        elif isinstance(b, Quote):
            out.extend(_render_quote(b, theme, width))
        elif isinstance(b, Table):
            out.extend(_render_table(b, theme, width))
        elif isinstance(b, Rule):
            out.append(theme["rule"] + ("\u2500" * width) + RESET)
        elif isinstance(b, FrontMatter):
            out.extend(_render_frontmatter(b, theme, width))
        out.append("")
    return out


def _render_heading(b, theme, width):
    style = theme["h1"] if b.level == 1 else theme["h2"] if b.level == 2 else theme["h_other"]
    lines = [style + render_inline(b.text, theme) + RESET]
    if b.level == 1:
        lines.append(theme["rule"] + ("\u2500" * width) + RESET)
    return lines


def _render_quote(b, theme, width):
    styled = theme["quote"] + render_inline(b.text, theme) + RESET
    return [theme["quote_bar"] + "\u2502 " + RESET + line for line in _wrap_styled(styled, max(width - 2, 10))]


def _render_frontmatter(b, theme, width):
    rule = theme["rule"] + ("\u2500" * width) + RESET
    lines = [rule]
    for k, v in b.pairs:
        lines.append(theme["fm_key"] + k + sgr(SGR["unbold"], SGR["fg_default"]) + ": " + theme["fm_value"] + v + RESET)
    lines.append(rule)
    return lines


def _render_list(b, theme, width):
    lines = []
    for item in b.items:
        indent = "  " * item.level
        if item.checked is not None:
            marker = ("\u2611" if item.checked else "\u2610") + " "
        elif item.ordered:
            marker = f"{item.number}. "
        else:
            bullet_char = "\u2022" if item.level % 2 == 0 else "\u25e6"
            marker = theme["bullet"] + bullet_char + RESET + " "
        prefix = indent + marker
        prefix_len = len(_strip_ansi(prefix))
        body = _wrap_styled(render_inline(item.text, theme), max(width - prefix_len, 10))
        lines.append(prefix + body[0])
        lines.extend(" " * prefix_len + cont for cont in body[1:])
    return lines


def _render_code(b, theme, width):
    bg = theme["code_bg"]
    num_width = len(str(len(b.lines))) if b.lines else 1
    border = sgr(SGR["dim"], bg) + (" " * width) + sgr(SGR["undim"], SGR["bg_default"])
    lines = [border]
    for idx, code_line in enumerate(b.lines, 1):
        highlighted = _highlight_code_line(code_line, b.lang.lower(), theme)
        num = theme["code_lineno"] + str(idx).rjust(num_width) + sgr(SGR["undim"], SGR["fg_default"])
        visible = num_width + 3 + len(_strip_ansi(highlighted))
        pad = " " * max(width - visible, 0)
        lines.append(sgr(bg) + num + " \u2502 " + highlighted + pad + sgr(SGR["bg_default"]))
    lines.append(border)
    return lines


def _table_col_widths(header, rows, total_width, ncols):
    natural = [len(_strip_ansi(header[c])) if c < len(header) else 0 for c in range(ncols)]
    for row in rows:
        for c in range(ncols):
            if c < len(row):
                natural[c] = max(natural[c], len(_strip_ansi(row[c])))
    budget = total_width - (ncols + 1) - ncols * 2  # border chars + 1-space padding each side
    if budget <= 0 or sum(natural) <= budget:
        return [max(w, 3) for w in natural]
    scale = budget / sum(natural) if sum(natural) else 1
    return [max(int(w * scale), 6) for w in natural]


def _table_border(col_width, left, mid, right, theme):
    segs = ["\u2500" * (w + 2) for w in col_width]
    return theme["table_border"] + left + mid.join(segs) + right + RESET


def _table_row(cells, col_width, aligns, theme, is_header):
    wrapped = [_wrap_styled(cells[c], col_width[c]) if c < len(cells) else [""] for c in range(len(col_width))]
    height = max((len(w) for w in wrapped), default=1)
    border = theme["table_border"]
    lines = []
    for r in range(height):
        parts = [border + "\u2502" + RESET]
        for c in range(len(col_width)):
            text = wrapped[c][r] if r < len(wrapped[c]) else ""
            if is_header:
                text = theme["table_header"] + text + sgr(SGR["unbold"])
            pad = max(col_width[c] - len(_strip_ansi(text)), 0)
            align = aligns[c] if c < len(aligns) else "l"
            if align == "r":
                cell = " " * pad + text
            elif align == "c":
                left = pad // 2
                cell = " " * left + text + " " * (pad - left)
            else:
                cell = text + " " * pad
            parts.append(" " + cell + " " + border + "\u2502" + RESET)
        lines.append("".join(parts))
    return lines


def _render_table(b, theme, width):
    styled_header = [render_inline(c, theme) for c in b.header]
    styled_rows = [[render_inline(c, theme) for c in row] for row in b.rows]
    ncols = len(b.header)
    aligns = (b.aligns + ["l"] * ncols)[:ncols]
    col_width = _table_col_widths(styled_header, styled_rows, width, ncols)
    lines = [_table_border(col_width, "\u250c", "\u252c", "\u2510", theme)]
    lines.extend(_table_row(styled_header, col_width, aligns, theme, True))
    lines.append(_table_border(col_width, "\u251c", "\u253c", "\u2524", theme))
    for row in styled_rows:
        lines.extend(_table_row(row, col_width, aligns, theme, False))
    lines.append(_table_border(col_width, "\u2514", "\u2534", "\u2518", theme))
    return lines


# ============================================================
# TOC / section extraction
# ============================================================
def render_toc(blocks, theme):
    lines = [theme["bullet"] + "  " * (b.level - 1) + "\u2022 " + RESET + render_inline(b.text, theme)
             for b in blocks if isinstance(b, Heading)]
    return lines or ["(no headings found)"]


def extract_section(blocks, name):
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
# CLI / main
# ============================================================
def _resolve_color(no_color_flag):
    if no_color_flag or os.environ.get("NO_COLOR", ""):
        return False
    if os.environ.get("FORCE_COLOR", ""):
        return True
    return sys.stdout.isatty()


def _load_config():
    cfg_dir = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    path = os.path.join(cfg_dir, "mdcat", "config")
    cfg = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                cfg[k.strip()] = v.strip()
    return cfg


def _file_header(name, theme, width):
    label = f" {name} "
    pad = max(width - len(label), 0)
    left = pad // 2
    return (theme["rule"] + ("\u2550" * left) + sgr(SGR["bold"]) + label + sgr(SGR["unbold"])
            + theme["rule"] + ("\u2550" * (pad - left)) + RESET)


def build_arg_parser():
    p = argparse.ArgumentParser(prog="mdcat", description="Render a Markdown file to the terminal.")
    p.add_argument("files", nargs="*", help="Markdown file(s) to render. Reads stdin if omitted or '-'.")
    p.add_argument("--width", type=int, help="Wrap width in columns (default: detect terminal width)")
    p.add_argument("--theme", choices=["dark", "light"], help="Color theme (default: dark, or config file)")
    p.add_argument("--no-color", "--plain", dest="no_color", action="store_true", help="Disable all styling")
    p.add_argument("--no-pager", action="store_true", help="Don't page long output through $PAGER/less")
    p.add_argument("--toc", action="store_true", help="Print only the heading outline")
    p.add_argument("--heading", metavar="NAME", help="Print only the section starting at this heading")
    p.add_argument("--version", action="version", version=f"mdcat {VERSION}")
    return p


def _emit(out_lines, use_color, no_pager_flag, cfg):
    text = "\n".join(out_lines)
    if not use_color:
        text = _strip_ansi(text)
    term_h = shutil.get_terminal_size((100, 24)).lines
    use_pager = (not no_pager_flag and cfg.get("pager") != "disabled"
                 and sys.stdout.isatty() and len(out_lines) > term_h)
    if use_pager:
        pager_cmd = os.environ.get("PAGER") or cfg.get("pager") or "less -R"
        try:
            proc = subprocess.Popen(pager_cmd.split(), stdin=subprocess.PIPE)
            proc.communicate(input=text.encode("utf-8"))
            return
        except OSError:
            pass  # pager unavailable -- fall through to a direct print
    print(text)


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    if not args.files and sys.stdin.isatty():
        build_arg_parser().print_help()
        return 1

    cfg = _load_config()
    cfg_width = int(cfg["width"]) if cfg.get("width", "").isdigit() else None
    width = args.width or cfg_width or shutil.get_terminal_size((100, 24)).columns
    theme_name = args.theme or cfg.get("theme") or "dark"
    theme = THEMES.get(theme_name, THEMES["dark"])
    use_color = _resolve_color(args.no_color)

    targets = args.files or ["-"]
    texts, had_error = [], False
    for f in targets:
        try:
            if f == "-":
                texts.append((None, sys.stdin.read()))
            else:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    texts.append((f, fh.read()))
        except OSError as e:
            print(f"mdcat: {f}: {e.strerror}", file=sys.stderr)
            had_error = True

    out_lines = []
    for name, text in texts:
        if name and len(targets) > 1:
            out_lines.append(_file_header(name, theme, width))
        blocks = parse_blocks(text.splitlines())
        if args.toc:
            out_lines.extend(render_toc(blocks, theme))
            out_lines.append("")
            continue
        if args.heading:
            section = extract_section(blocks, args.heading)
            if section is None:
                print(f'mdcat: no heading matching "{args.heading}" found in {name or "input"}', file=sys.stderr)
                had_error = True
                continue
            blocks = section
        out_lines.extend(render_blocks(blocks, theme, width))

    _emit(out_lines, use_color, args.no_pager, cfg)
    return 1 if had_error else 0


if __name__ == "__main__":
    sys.exit(main())
