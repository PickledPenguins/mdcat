#!/usr/bin/env python3
"""Test suite for mdcat.

Run with:   python3 -m unittest test_mdcat -v
Coverage:   python3 -m coverage run --branch -m unittest test_mdcat
            python3 -m coverage report -m --include=mdcat.py

The suite is organised as unit tests over each pipeline stage, integration
tests that drive the command line in-process, and a small number of
subprocess smoke tests that exercise behaviour only observable in a real
process (broken pipes, restricted output encodings, exit codes).
"""
import ast
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mdcat as m

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "mdcat.py")
SAMPLE = os.path.join(HERE, "sample.md")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def blocks(text):
    """Parse Markdown text into the block AST."""
    return m.parse_blocks(m._prepare_lines(text))


def render(text, width=80, theme="dark", ascii_only=False):
    """Render Markdown text to styled terminal lines."""
    return m.render_blocks(blocks(text), m.build_theme(theme, ascii_only), width)


def plain(text, width=80, **kwargs):
    """Render Markdown text and remove the styling, for content assertions."""
    return [m._strip_ansi(line) for line in render(text, width, **kwargs)]


def widths(lines):
    """Return the display width of each rendered line."""
    return [m._visible_len(line) for line in lines]


@contextlib.contextmanager
def cli_environment(**overrides):
    """Run with a controlled environment: no user config, fixed terminal size."""
    saved = dict(os.environ)
    with tempfile.TemporaryDirectory() as empty:
        os.environ.pop("NO_COLOR", None)
        os.environ.pop("FORCE_COLOR", None)
        os.environ.pop("PAGER", None)
        os.environ["XDG_CONFIG_HOME"] = empty
        os.environ["COLUMNS"] = "80"
        os.environ["LINES"] = "24"
        os.environ.update({k: v for k, v in overrides.items() if v is not None})
        for key, value in overrides.items():
            if value is None:
                os.environ.pop(key, None)
        try:
            yield
        finally:
            os.environ.clear()
            os.environ.update(saved)


def run_cli(args, stdin="", **env):
    """Call main() in-process and return (exit_code, stdout, stderr)."""
    out, err, in_stream = io.StringIO(), io.StringIO(), io.StringIO(stdin)
    saved = (sys.stdout, sys.stderr, sys.stdin)
    with cli_environment(**env):
        sys.stdout, sys.stderr, sys.stdin = out, err, in_stream
        try:
            code = m.main(args)
        finally:
            sys.stdout, sys.stderr, sys.stdin = saved
    return code, out.getvalue(), err.getvalue()


def run_process(args, env=None, stdin=None):
    """Run mdcat.py as a separate process and return the completed process."""
    environment = dict(os.environ, XDG_CONFIG_HOME=tempfile.gettempdir() + "/mdcat-none")
    environment.update(env or {})
    return subprocess.run([sys.executable, SCRIPT] + args, input=stdin,
                          capture_output=True, text=True, env=environment, check=False)


# ------------------------------------------------------------------
# Input sanitisation
# ------------------------------------------------------------------
class TestSanitization(unittest.TestCase):
    def test_tabs_expanded_to_spaces(self):
        self.assertEqual(m._prepare_lines("a\tb"), ["a   b"])

    def test_csi_sequences_removed(self):
        self.assertEqual(m._prepare_lines("a \x1b[31mred\x1b[0m b"), ["a red b"])

    def test_osc_sequences_removed(self):
        self.assertEqual(m._prepare_lines("t \x1b]0;title\x07 x"), ["t  x"])

    def test_cursor_and_erase_sequences_removed(self):
        self.assertEqual(m._prepare_lines("a\x1b[2Jb\x1b[Hc"), ["abc"])

    def test_control_characters_removed(self):
        self.assertEqual(m._prepare_lines("a\x00b\x01c\x7fd"), ["abcd"])

    def test_nul_cannot_forge_a_code_span_placeholder(self):
        # NUL is the internal placeholder marker; a document containing one
        # must not be able to substitute unrelated content into the output.
        self.assertEqual(plain("a \x000\x00 b `z`")[0], "a 0 b z")

    def test_sanitising_preserves_ordinary_unicode(self):
        self.assertEqual(m._prepare_lines("caf\u00e9 \u65e5\u672c"), ["caf\u00e9 \u65e5\u672c"])


# ------------------------------------------------------------------
# Measurement helpers
# ------------------------------------------------------------------
class TestMeasurement(unittest.TestCase):
    def test_ascii_width(self):
        self.assertEqual(m._display_width("hello"), 5)

    def test_east_asian_characters_are_two_columns(self):
        self.assertEqual(m._display_width("\u65e5\u672c\u8a9e"), 6)

    def test_combining_marks_are_zero_width(self):
        self.assertEqual(m._display_width("e\u0301"), 1)

    def test_visible_length_ignores_escapes(self):
        self.assertEqual(m._visible_len("\x1b[1mabc\x1b[22m"), 3)

    def test_chunking_respects_display_width(self):
        chunks = m._chunk_styled("\u65e5\u672c\u8a9e\u30c6", 4)
        self.assertEqual(chunks, ["\u65e5\u672c", "\u8a9e\u30c6"])

    def test_chunking_never_splits_an_escape_sequence(self):
        chunks = m._chunk_styled("\x1b[1mabcdef\x1b[22m", 3)
        self.assertEqual([m._strip_ansi(c) for c in chunks], ["abc", "def"])
        for chunk in chunks:
            self.assertNotIn("\x1b[", m._strip_ansi(chunk))

    def test_chunking_empty_string(self):
        self.assertEqual(m._chunk_styled("", 5), [""])


# ------------------------------------------------------------------
# Headings
# ------------------------------------------------------------------
class TestHeadings(unittest.TestCase):
    def test_levels(self):
        parsed = blocks("# H1\n## H2\n###### H6")
        self.assertEqual([b.level for b in parsed], [1, 2, 6])
        self.assertEqual([b.text for b in parsed], ["H1", "H2", "H6"])

    def test_closing_hashes_stripped_when_spaced(self):
        self.assertEqual(blocks("## Title ##")[0].text, "Title")

    def test_trailing_hash_kept_when_part_of_the_word(self):
        # Regression: '# C#' previously rendered as 'C'.
        self.assertEqual(blocks("# C#")[0].text, "C#")
        self.assertEqual(blocks("## Q##")[0].text, "Q##")

    def test_hash_without_space_is_not_a_heading(self):
        self.assertIsInstance(blocks("#hashtag")[0], m.Para)

    def test_empty_heading(self):
        self.assertEqual(blocks("#")[0].text, "")

    def test_level_one_gets_a_rule(self):
        self.assertEqual(len(plain("# Title", 30)), 3)  # text, rule, spacer

    def test_inline_code_in_heading(self):
        self.assertIn("mb guide", plain("# `mb` guide")[0])

    def test_seven_hashes_is_not_a_heading(self):
        self.assertIsInstance(blocks("####### too deep")[0], m.Para)


# ------------------------------------------------------------------
# Paragraphs
# ------------------------------------------------------------------
class TestParagraphs(unittest.TestCase):
    def test_reflow_joins_source_lines(self):
        parsed = blocks("This is one\nparagraph split\nacross lines.")
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].text, "This is one paragraph split across lines.")

    def test_blank_line_separates_paragraphs(self):
        self.assertEqual(len(blocks("First.\n\nSecond.")), 2)

    def test_wrapping_respects_width(self):
        lines = plain("word " * 40, 30)
        self.assertTrue(all(w <= 30 for w in widths(lines)))

    def test_overlong_word_is_broken_rather_than_overflowing(self):
        lines = plain("see https://example.com/" + "x" * 80, 30)
        self.assertTrue(all(w <= 30 for w in widths(lines)))

    def test_wide_characters_do_not_overflow(self):
        lines = plain("\u65e5\u672c\u8a9e " * 20, 30)
        self.assertTrue(all(w <= 30 for w in widths(lines)))


# ------------------------------------------------------------------
# Code blocks
# ------------------------------------------------------------------
class TestCodeBlocks(unittest.TestCase):
    def test_basic_fence(self):
        parsed = blocks("```python\nx = 1\n```")
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].lang, "python")
        self.assertEqual(parsed[0].lines, ["x = 1"])

    def test_tilde_fence(self):
        self.assertEqual(blocks("~~~\nplain\n~~~")[0].lines, ["plain"])

    def test_fence_length_awareness(self):
        parsed = blocks("````\n```\ninner\n```\n````")
        self.assertEqual(parsed[0].lines, ["```", "inner", "```"])

    def test_unclosed_fence_runs_to_end_of_document(self):
        self.assertEqual(blocks("```\na\nb")[0].lines, ["a", "b"])

    def test_fence_indentation_is_removed_from_the_body(self):
        parsed = blocks("  ```\n  indented\n  ```")
        self.assertEqual(parsed[0].lines, ["indented"])

    def test_indented_code_block(self):
        parsed = blocks("    x = 1\n    y = 2")
        self.assertIsInstance(parsed[0], m.CodeBlock)
        self.assertEqual(parsed[0].lines, ["x = 1", "y = 2"])

    def test_indented_lines_after_a_paragraph_stay_in_the_paragraph(self):
        parsed = blocks("text\n    still text")
        self.assertEqual(len(parsed), 1)
        self.assertIsInstance(parsed[0], m.Para)

    def test_line_numbers_are_rendered(self):
        lines = plain("```\na\nb\n```", 40)
        self.assertIn("1 ", lines[1])
        self.assertIn("2 ", lines[2])

    def test_panel_lines_all_fill_the_width(self):
        lines = plain("```\nshort\n```", 40)[:-1]
        self.assertTrue(all(w == 40 for w in widths(lines)), widths(lines))

    def test_tabs_do_not_break_panel_padding(self):
        # Regression: a tab counted as one column left the panel ragged.
        lines = plain("```\n\tindented\n```", 40)[:-1]
        self.assertTrue(all(w == 40 for w in widths(lines)), widths(lines))

    def test_long_code_line_wraps_inside_the_panel(self):
        lines = plain("```\n" + "y" * 90 + "\n```", 40)[:-1]
        self.assertTrue(all(w == 40 for w in widths(lines)), widths(lines))
        self.assertGreater(len(lines), 3)  # opened onto continuation rows

    def test_wide_characters_in_code_do_not_break_padding(self):
        lines = plain("```\n\u65e5\u672c\u8a9e\n```", 40)[:-1]
        self.assertTrue(all(w == 40 for w in widths(lines)), widths(lines))

    def test_empty_code_block(self):
        lines = plain("```\n```", 30)
        self.assertEqual(widths(lines[:-1]), [30, 30])


# ------------------------------------------------------------------
# Lists
# ------------------------------------------------------------------
class TestLists(unittest.TestCase):
    def test_flat_bullets(self):
        items = blocks("- a\n- b")[0].items
        self.assertEqual([i.text for i in items], ["a", "b"])
        self.assertEqual([i.level for i in items], [0, 0])

    def test_nesting_levels(self):
        items = blocks("- a\n  - b\n    - c\n- d")[0].items
        self.assertEqual([i.level for i in items], [0, 1, 2, 0])

    def test_ordered_numbering(self):
        items = blocks("1. a\n2. b\n3. c")[0].items
        self.assertEqual([i.number for i in items], [1, 2, 3])

    def test_ordered_list_honours_an_explicit_start(self):
        self.assertEqual([i.number for i in blocks("5. a\n6. b")[0].items], [5, 6])

    def test_nested_counters_restart_per_sublist(self):
        # Regression: the second sublist previously continued from the first.
        items = blocks("1. one\n   1. a\n   2. b\n2. two\n   1. c")[0].items
        self.assertEqual([(i.level, i.number) for i in items],
                         [(0, 1), (1, 1), (1, 2), (0, 2), (1, 1)])

    def test_paren_markers(self):
        self.assertEqual([i.number for i in blocks("1) a\n2) b")[0].items], [1, 2])

    def test_task_items(self):
        items = blocks("- [x] done\n- [ ] todo")[0].items
        self.assertEqual([i.checked for i in items], [True, False])
        self.assertEqual([i.text for i in items], ["done", "todo"])

    def test_task_item_with_no_text(self):
        self.assertEqual(blocks("- [x]")[0].items[0].text, "")

    def test_wrapped_continuation_joins_item_text(self):
        items = blocks("- first part\n  second part")[0].items
        self.assertEqual(items[0].text, "first part second part")

    def test_fenced_code_inside_a_list_item_is_preserved(self):
        # Regression: the fence and its contents were previously absorbed
        # into the item's own text, destroying the code sample.
        items = blocks("- item one\n  ```bash\n  echo hi\n  ```\n- item two")[0].items
        self.assertEqual([i.text for i in items], ["item one", "item two"])
        self.assertEqual(len(items[0].children), 1)
        self.assertEqual(items[0].children[0].lines, ["echo hi"])
        self.assertEqual(items[0].children[0].lang, "bash")

    def test_fenced_code_after_a_blank_line_stays_with_its_item(self):
        parsed = blocks("- item one\n\n  ```\n  code\n  ```\n\n- item two")
        self.assertEqual(len(parsed), 1)
        items = parsed[0].items
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].children[0].lines, ["code"])

    def test_text_after_a_child_block_becomes_a_paragraph(self):
        items = blocks("- item\n  ```\n  code\n  ```\n  trailing note")[0].items
        self.assertIsInstance(items[0].children[1], m.Para)
        self.assertEqual(items[0].children[1].text, "trailing note")

    def test_nested_code_renders_indented_under_the_item(self):
        lines = plain("- item\n  ```\n  code\n  ```", 40)
        panel = [line for line in lines if "code" in line]
        self.assertTrue(panel[0].startswith("  "), panel)

    def test_hanging_indent_on_wrapped_items(self):
        lines = plain("- " + "word " * 20, 30)
        self.assertTrue(lines[1].startswith("  "))
        self.assertTrue(all(w <= 30 for w in widths(lines)))

    def test_horizontal_rule_wins_over_a_list_marker(self):
        self.assertIsInstance(blocks("- - -")[0], m.Rule)

    def test_list_ends_at_unindented_text(self):
        parsed = blocks("- item\nnot a list")
        self.assertEqual(len(parsed), 2)
        self.assertIsInstance(parsed[1], m.Para)


# ------------------------------------------------------------------
# Blockquotes
# ------------------------------------------------------------------
class TestQuotes(unittest.TestCase):
    def test_simple_quote_is_reflowed(self):
        parsed = blocks("> one\n> two")
        self.assertEqual(len(parsed[0].blocks), 1)
        self.assertEqual(parsed[0].blocks[0].text, "one two")

    def test_quote_bar_prefixes_every_line(self):
        lines = plain("> " + "word " * 30, 40)[:-1]
        self.assertTrue(all(line.startswith("\u2502 ") for line in lines), lines)

    def test_wrapped_quote_keeps_its_styling(self):
        # Regression: continuation lines lost the italic quote style because
        # the bar prefix issued a full reset.
        lines = render("> " + "word " * 30, 40)[:-1]
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertIn(m.THEMES["dark"]["quote"], line)

    def test_list_inside_a_quote_keeps_its_structure(self):
        # Regression: nested structure previously collapsed onto one line.
        parsed = blocks("> - a\n> - b")
        self.assertIsInstance(parsed[0].blocks[0], m.ListBlock)
        lines = [line for line in plain("> - a\n> - b", 40) if line.strip()]
        self.assertEqual(len(lines), 2)

    def test_code_inside_a_quote(self):
        parsed = blocks("> text\n>\n> ```\n> code\n> ```")
        kinds = [type(b) for b in parsed[0].blocks]
        self.assertIn(m.CodeBlock, kinds)

    def test_quote_width_stays_within_the_terminal(self):
        lines = plain("> " + "word " * 40, 40)
        self.assertTrue(all(w <= 40 for w in widths(lines)))


# ------------------------------------------------------------------
# Tables
# ------------------------------------------------------------------
class TestTables(unittest.TestCase):
    def test_basic_table(self):
        parsed = blocks("| a | b |\n|---|---|\n| 1 | 2 |")[0]
        self.assertEqual(parsed.header, ["a", "b"])
        self.assertEqual(parsed.rows, [["1", "2"]])

    def test_alignment_markers(self):
        parsed = blocks("| l | c | r |\n|:--|:-:|--:|\n| 1 | 2 | 3 |")[0]
        self.assertEqual(parsed.aligns, ["l", "c", "r"])

    def test_right_alignment_is_applied(self):
        lines = plain("| n |\n|--:|\n| 1 |", 30)
        self.assertTrue(lines[3].rstrip().endswith("1 \u2502"))

    def test_escaped_pipe_becomes_a_literal_pipe(self):
        # Regression: the backslash was left in the rendered cell.
        parsed = blocks("| a | b |\n|---|---|\n| x \\| y | z |")[0]
        self.assertEqual(parsed.rows, [["x | y", "z"]])

    def test_pipe_inside_a_code_span_does_not_split_the_cell(self):
        # Regression: the cell was previously split in two.
        parsed = blocks("| cmd | d |\n|---|---|\n| `a|b` | pipe |")[0]
        self.assertEqual(parsed.rows, [["`a|b`", "pipe"]])

    def test_extra_cells_are_kept_not_dropped(self):
        lines = plain("| a | b |\n|---|---|\n| 1 | 2 | 3 |", 40)
        self.assertIn("3", lines[3])

    def test_missing_cells_are_padded(self):
        lines = plain("| a | b |\n|---|---|\n| 1 |", 40)
        self.assertEqual(len(set(widths(lines[:-1]))), 1)

    def test_all_rows_share_one_width(self):
        lines = plain("| a | bbbb |\n|---|---|\n| 1 | 2 |", 40)[:-1]
        self.assertEqual(len(set(widths(lines))), 1, widths(lines))

    def test_wide_characters_keep_columns_aligned(self):
        # Regression: CJK text made every row a different rendered width.
        lines = plain("| n | v |\n|---|---|\n| \u65e5\u672c\u8a9e | 1 |", 40)[:-1]
        self.assertEqual(len(set(widths(lines))), 1, widths(lines))

    def test_wide_table_is_shrunk_within_the_width(self):
        # Regression: a fixed per-column floor let wide tables overflow.
        header = "| " + " | ".join(f"col{i}" for i in range(8)) + " |"
        sep = "|" + "---|" * 8
        row = "| " + " | ".join("x" * 12 for _ in range(8)) + " |"
        lines = plain(f"{header}\n{sep}\n{row}", 40)[:-1]
        self.assertTrue(all(w <= 40 for w in widths(lines)), widths(lines))

    def test_table_cell_contents_are_styled(self):
        lines = plain("| **b** |\n|---|\n| x |", 30)
        self.assertIn("b", lines[1])
        self.assertNotIn("**", lines[1])

    def test_narrow_table_falls_back_to_labelled_records(self):
        header = "| " + " | ".join(f"c{i}" for i in range(12)) + " |"
        sep = "|" + "---|" * 12
        row = "| " + " | ".join(f"v{i}" for i in range(12)) + " |"
        lines = plain(f"{header}\n{sep}\n{row}", 30)
        self.assertIn("c0: v0", lines)
        self.assertNotIn("\u252c", "".join(lines))  # no grid was drawn
        self.assertTrue(all(w <= 30 for w in widths(lines)), widths(lines))

    def test_record_fallback_separates_rows(self):
        header = "| " + " | ".join(f"c{i}" for i in range(12)) + " |"
        sep = "|" + "---|" * 12
        rows = "\n".join("| " + " | ".join(f"r{n}v{i}" for i in range(12)) + " |"
                         for n in range(2))
        lines = plain(f"{header}\n{sep}\n{rows}", 30)
        self.assertIn("", lines[12:14])

    def test_record_fallback_with_no_body_rows(self):
        header = "| " + " | ".join(f"c{i}" for i in range(12)) + " |"
        lines = plain(header + "\n" + "|" + "---|" * 12, 30)
        self.assertIn("c0", lines)

    def test_record_fallback_labels_unnamed_columns(self):
        header = "|  |  |  |  |  |  |  |  |  |  |  |  |"
        sep = "|" + "---|" * 12
        row = "| " + " | ".join(f"v{i}" for i in range(12)) + " |"
        lines = plain(f"{header}\n{sep}\n{row}", 30)
        self.assertIn("column 1: v0", lines)

    def test_grid_is_used_when_it_fits(self):
        header = "| " + " | ".join(f"c{i}" for i in range(12)) + " |"
        sep = "|" + "---|" * 12
        row = "| " + " | ".join(f"v{i}" for i in range(12)) + " |"
        lines = plain(f"{header}\n{sep}\n{row}", 100)
        self.assertIn("\u252c", "".join(lines))

    def test_column_width_helper_degrades_without_a_budget(self):
        self.assertEqual(m._table_col_widths(["a"] * 6, [], 10, 6), [1] * 6)

    def test_table_needs_a_separator_row(self):
        self.assertIsInstance(blocks("| a | b |\n| 1 | 2 |")[0], m.Para)


# ------------------------------------------------------------------
# Inline rendering
# ------------------------------------------------------------------
class TestInline(unittest.TestCase):
    def setUp(self):
        self.theme = m.build_theme("dark")

    def rendered(self, text):
        return m._strip_ansi(m.render_inline(text, self.theme))

    def test_bold_and_italic_markers_removed(self):
        self.assertEqual(self.rendered("**b** and *i*"), "b and i")

    def test_underscore_forms(self):
        self.assertEqual(self.rendered("__b__ and _i_"), "b and i")

    def test_bold_italic_combined(self):
        self.assertEqual(self.rendered("***both***"), "both")

    def test_strikethrough_and_highlight(self):
        self.assertEqual(self.rendered("~~gone~~ and ==lit=="), "gone and lit")

    def test_snake_case_is_not_emphasis(self):
        self.assertEqual(self.rendered("snake_case_name"), "snake_case_name")

    def test_exponent_is_not_emphasis(self):
        self.assertEqual(self.rendered("2**10"), "2**10")

    def test_code_span(self):
        self.assertEqual(self.rendered("use `ls -l` here"), "use ls -l here")

    def test_double_backtick_code_span(self):
        self.assertEqual(self.rendered("`` a ` b ``"), "a ` b")

    def test_emphasis_inside_a_code_span_is_literal(self):
        self.assertEqual(self.rendered("`**not bold**`"), "**not bold**")

    def test_link_renders_label_and_url(self):
        self.assertEqual(self.rendered("[docs](http://x)"), "docs (http://x)")

    def test_autolink(self):
        self.assertEqual(self.rendered("<https://example.com>"), "https://example.com")

    def test_image_placeholder(self):
        self.assertEqual(self.rendered("![alt](x.png)"), "[image: alt]")

    def test_image_without_alt_text(self):
        self.assertEqual(self.rendered("![](x.png)"), "[image: image]")

    def test_link_does_not_clobber_surrounding_bold(self):
        # Regression: the link ended with a full reset, cancelling the bold.
        out = m.render_inline("**see [d](http://x) now**", self.theme)
        self.assertNotIn(m.RESET, out)
        self.assertTrue(out.endswith(m._UNBOLD))

    def test_image_does_not_clobber_surrounding_bold(self):
        out = m.render_inline("**an ![a](x.png) here**", self.theme)
        self.assertNotIn(m.RESET, out)

    def test_nested_emphasis_inside_a_link_label(self):
        self.assertEqual(self.rendered("[**bold** label](u)"), "bold label (u)")


# ------------------------------------------------------------------
# Wrapping and style repair
# ------------------------------------------------------------------
class TestWrapping(unittest.TestCase):
    def test_background_is_closed_and_reopened_across_a_wrap(self):
        # Regression: an open background painted the rest of the terminal row.
        theme = m.build_theme("dark")
        text = "word " * 6 + "`a long inline code span that wraps` tail"
        lines = m._wrap_styled(m.render_inline(text, theme), 40)
        self.assertGreater(len(lines), 1)
        self.assertTrue(lines[0].endswith(m.sgr(m.SGR["bg_default"])))
        self.assertTrue(lines[1].startswith(m.sgr(theme["code_bg"])))

    def test_no_background_means_no_extra_codes(self):
        lines = m._wrap_styled("plain words here", 8)
        self.assertEqual(lines, ["plain", "words", "here"])

    def test_track_background_handles_extended_colours(self):
        self.assertEqual(m._track_background("\x1b[38;5;217;48;5;236m", None), "48;5;236")

    def test_track_background_cleared_by_reset(self):
        self.assertIsNone(m._track_background("\x1b[48;5;236m\x1b[0m", None))

    def test_track_background_cleared_by_49(self):
        self.assertIsNone(m._track_background("\x1b[48;5;236m\x1b[49m", None))

    def test_track_background_basic_colour(self):
        self.assertEqual(m._track_background("\x1b[41m", None), "41")

    def test_empty_input_wraps_to_one_empty_line(self):
        self.assertEqual(m._wrap_styled("", 10), [""])


# ------------------------------------------------------------------
# Syntax highlighting
# ------------------------------------------------------------------
class TestHighlighting(unittest.TestCase):
    def setUp(self):
        self.theme = m.build_theme("dark")

    def test_python_keywords_highlighted(self):
        out = m._highlight_code_line("def f():", "python", self.theme)
        self.assertIn(self.theme["code_keyword"], out)

    def test_shell_keywords_highlighted(self):
        out = m._highlight_code_line("for x in y; do", "bash", self.theme)
        self.assertIn(self.theme["code_keyword"], out)

    def test_unknown_language_is_left_alone(self):
        self.assertEqual(m._highlight_code_line("def f():", "rust", self.theme), "def f():")

    def test_strings_highlighted(self):
        out = m._highlight_code_line('x = "hello"', "python", self.theme)
        self.assertIn(self.theme["code_string"], out)

    def test_comments_highlighted(self):
        out = m._highlight_code_line("x = 1  # note", "python", self.theme)
        self.assertIn(self.theme["code_comment"], out)

    def test_hash_inside_a_string_is_not_a_comment(self):
        out = m._highlight_code_line('x = "a # b"', "python", self.theme)
        self.assertNotIn(self.theme["code_comment"], out)

    def test_highlighting_preserves_the_visible_text(self):
        source = 'if x == "a":  # check'
        out = m._highlight_code_line(source, "python", self.theme)
        self.assertEqual(m._strip_ansi(out), source)


# ------------------------------------------------------------------
# Frontmatter, rules, outline, sections
# ------------------------------------------------------------------
class TestFrontMatter(unittest.TestCase):
    def test_parsed_into_pairs(self):
        parsed = blocks("---\ntitle: T\nversion: 2\n---\n\nbody")
        self.assertEqual(parsed[0].pairs, [("title", "T"), ("version", "2")])

    def test_blank_and_comment_lines_allowed(self):
        parsed = blocks("---\ntitle: T\n\n# a note\nk: v\n---\n")
        self.assertEqual(parsed[0].pairs, [("title", "T"), ("k", "v")])

    def test_non_frontmatter_leading_rule(self):
        parsed = blocks("---\njust a paragraph\n---\n")
        self.assertIsInstance(parsed[0], m.Rule)

    def test_unterminated_block_is_not_frontmatter(self):
        self.assertIsInstance(blocks("---\nk: v\n")[0], m.Rule)

    def test_rendered_between_two_rules(self):
        lines = plain("---\nk: v\n---\n", 30)
        self.assertEqual(lines[0], "\u2500" * 30)
        self.assertEqual(lines[2], "\u2500" * 30)


class TestRules(unittest.TestCase):
    def test_dash_star_underscore_forms(self):
        for source in ("---", "***", "___", "- - -"):
            self.assertIsInstance(blocks(source)[0], m.Rule, source)

    def test_rule_fills_the_width(self):
        self.assertEqual(plain("***", 25)[0], "\u2500" * 25)


class TestOutline(unittest.TestCase):
    def test_toc_lists_headings_with_indentation(self):
        lines = [m._strip_ansi(line)
                 for line in m.render_toc(blocks("# A\n## B\n### C"), m.build_theme())]
        self.assertEqual(lines, ["\u2022 A", "  \u2022 B", "    \u2022 C"])

    def test_toc_without_headings(self):
        self.assertEqual(m.render_toc(blocks("text"), m.build_theme()), ["(no headings found)"])

    def test_section_extraction_stops_at_the_same_level(self):
        parsed = blocks("# A\n\ntext a\n\n## B\n\ntext b\n\n# C\n\ntext c")
        section = m.extract_section(parsed, "A")
        self.assertEqual([b.text for b in section if isinstance(b, m.Heading)], ["A", "B"])

    def test_section_extraction_is_case_insensitive(self):
        self.assertIsNotNone(m.extract_section(blocks("## Common Mistakes\n\nx"), "common mistakes"))

    def test_section_extraction_matches_a_substring(self):
        self.assertIsNotNone(m.extract_section(blocks("## Common Mistakes\n\nx"), "mistakes"))

    def test_section_extraction_returns_none_when_absent(self):
        self.assertIsNone(m.extract_section(blocks("## A\n\nx"), "nope"))


# ------------------------------------------------------------------
# Themes and glyph sets
# ------------------------------------------------------------------
class TestThemes(unittest.TestCase):
    def test_known_themes_differ(self):
        self.assertNotEqual(m.build_theme("dark")["h1"], m.build_theme("light")["h1"])

    def test_unknown_theme_falls_back_to_dark(self):
        self.assertEqual(m.build_theme("nope")["h1"], m.THEMES["dark"]["h1"])

    def test_ascii_mode_avoids_non_ascii_glyphs(self):
        lines = plain("| a |\n|---|\n| 1 |\n\n- x\n\n> q\n\n---", 30, ascii_only=True)
        for line in lines:
            line.encode("ascii")  # raises if a box-drawing glyph leaked through

    def test_glyph_sets_cover_the_same_keys(self):
        self.assertEqual(set(m.GLYPHS_UNICODE), set(m.GLYPHS_ASCII))

    def test_glyph_keys_do_not_collide_with_colour_keys(self):
        self.assertFalse(set(m.GLYPHS_UNICODE) & set(m.THEMES["dark"]))


# ------------------------------------------------------------------
# Configuration file
# ------------------------------------------------------------------
class TestConfig(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "mdcat"))
        self.path = os.path.join(self.dir, "mdcat", "config")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def load(self):
        with cli_environment(XDG_CONFIG_HOME=self.dir):
            return m._load_config()

    def test_values_parsed(self):
        self.write("width = 70\ntheme = light\n")
        self.assertEqual(self.load(), {"width": "70", "theme": "light"})

    def test_comments_and_blank_lines_ignored(self):
        self.write("# comment\n\nwidth = 70\n")
        self.assertEqual(self.load(), {"width": "70"})

    def test_missing_file_gives_empty_config(self):
        with cli_environment(XDG_CONFIG_HOME=tempfile.mkdtemp()):
            self.assertEqual(m._load_config(), {})

    def test_unreadable_config_is_ignored(self):
        # A directory where the config file is expected must not raise.
        os.makedirs(os.path.join(self.dir, "mdcat", "config", "sub"), exist_ok=True)
        self.assertEqual(self.load(), {})

    def test_valid_width_accepted(self):
        self.assertEqual(m._config_width({"width": "70"}), 70)

    def test_width_below_the_minimum_rejected(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertIsNone(m._config_width({"width": "3"}))
        self.assertIn("invalid config width", stderr.getvalue())

    def test_non_numeric_width_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(m._config_width({"width": "wide"}))

    def test_absent_width_is_silent(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertIsNone(m._config_width({}))
        self.assertEqual(stderr.getvalue(), "")

    def test_unknown_theme_rejected_with_a_warning(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertIsNone(m._config_theme({"theme": "solarized"}))
        self.assertIn("unknown config theme", stderr.getvalue())

    def test_valid_theme_accepted(self):
        self.assertEqual(m._config_theme({"theme": "light"}), "light")


# ------------------------------------------------------------------
# Colour resolution and terminal probing
# ------------------------------------------------------------------
class TestEnvironment(unittest.TestCase):
    def test_no_color_flag_disables_colour(self):
        with cli_environment():
            self.assertFalse(m._resolve_color(True))

    def test_no_color_variable_disables_colour(self):
        with cli_environment(NO_COLOR="1"):
            self.assertFalse(m._resolve_color(False))

    def test_force_color_enables_colour_when_piped(self):
        with cli_environment(FORCE_COLOR="1"):
            self.assertTrue(m._resolve_color(False))

    def test_no_color_beats_force_color(self):
        with cli_environment(NO_COLOR="1", FORCE_COLOR="1"):
            self.assertFalse(m._resolve_color(False))

    def test_isatty_tolerates_a_stream_without_the_method(self):
        self.assertFalse(m._isatty(object()))

    def test_unicode_support_probe_rejects_ascii(self):
        class AsciiStream:
            encoding = "ascii"
        saved, sys.stdout = sys.stdout, AsciiStream()
        try:
            self.assertFalse(m._supports_unicode())
        finally:
            sys.stdout = saved

    def test_unicode_support_probe_accepts_utf8(self):
        class Utf8Stream:
            encoding = "utf-8"
        saved, sys.stdout = sys.stdout, Utf8Stream()
        try:
            self.assertTrue(m._supports_unicode())
        finally:
            sys.stdout = saved


# ------------------------------------------------------------------
# Command line, in-process
# ------------------------------------------------------------------
class TestCLI(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_renders_a_file(self):
        path = self.write("a.md", "# Title\n\nbody text\n")
        code, out, err = run_cli([path])
        self.assertEqual(code, 0)
        self.assertIn("Title", out)
        self.assertIn("body text", out)
        self.assertEqual(err, "")

    def test_reads_stdin_when_no_file_given(self):
        code, out, _ = run_cli([], stdin="# Piped\n")
        self.assertEqual(code, 0)
        self.assertIn("Piped", out)

    def test_dash_means_stdin(self):
        code, out, _ = run_cli(["-"], stdin="# Dash\n")
        self.assertEqual(code, 0)
        self.assertIn("Dash", out)

    def test_missing_file_reports_and_exits_one(self):
        code, out, err = run_cli([os.path.join(self.dir, "nope.md")])
        self.assertEqual(code, 1)
        self.assertIn("No such file", err)

    def test_missing_file_writes_nothing_to_stdout(self):
        # Regression: an empty render still emitted a stray blank line.
        _, out, _ = run_cli([os.path.join(self.dir, "nope.md")])
        self.assertEqual(out, "")

    def test_directory_argument_reports_cleanly(self):
        code, _, err = run_cli([self.dir])
        self.assertEqual(code, 1)
        self.assertIn("directory", err.lower())

    def test_one_bad_file_among_several_still_renders_the_rest(self):
        good = self.write("g.md", "# Good\n")
        code, out, err = run_cli([good, os.path.join(self.dir, "bad.md")])
        self.assertEqual(code, 1)
        self.assertIn("Good", out)
        self.assertIn("No such file", err)

    def test_multiple_files_get_name_banners(self):
        first = self.write("one.md", "# One\n")
        second = self.write("two.md", "# Two\n")
        _, out, _ = run_cli([first, second])
        self.assertIn("one.md", out)
        self.assertIn("two.md", out)

    def test_single_file_gets_no_banner(self):
        path = self.write("only.md", "# Only\n")
        _, out, _ = run_cli([path])
        self.assertNotIn("only.md", out)

    def test_output_is_plain_when_not_a_terminal(self):
        path = self.write("a.md", "# Title\n")
        _, out, _ = run_cli([path])
        self.assertNotIn("\x1b[", out)

    def test_force_color_emits_escapes(self):
        path = self.write("a.md", "# Title\n")
        _, out, _ = run_cli([path], FORCE_COLOR="1")
        self.assertIn("\x1b[", out)

    def test_no_color_flag_beats_force_color(self):
        path = self.write("a.md", "# Title\n")
        _, out, _ = run_cli(["--no-color", path], FORCE_COLOR="1")
        self.assertNotIn("\x1b[", out)

    def test_plain_is_an_alias_for_no_color(self):
        path = self.write("a.md", "# Title\n")
        _, out, _ = run_cli(["--plain", path], FORCE_COLOR="1")
        self.assertNotIn("\x1b[", out)

    def test_width_flag_controls_wrapping(self):
        path = self.write("a.md", "word " * 60)
        _, out, _ = run_cli(["--width", "30", path])
        self.assertTrue(all(len(line) <= 30 for line in out.splitlines()))

    def test_width_below_minimum_is_rejected(self):
        path = self.write("a.md", "x")
        with self.assertRaises(SystemExit) as caught:
            run_cli(["--width", "5", path])
        self.assertEqual(caught.exception.code, 2)

    def test_negative_width_is_rejected(self):
        path = self.write("a.md", "x")
        with self.assertRaises(SystemExit):
            run_cli(["--width", "-5", path])

    def test_toc_prints_only_headings(self):
        path = self.write("a.md", "# A\n\nbody\n\n## B\n\nmore\n")
        _, out, _ = run_cli(["--toc", path])
        self.assertIn("A", out)
        self.assertNotIn("body", out)

    def test_heading_prints_one_section(self):
        path = self.write("a.md", "# A\n\ntext a\n\n# B\n\ntext b\n")
        _, out, _ = run_cli(["--heading", "B", path])
        self.assertIn("text b", out)
        self.assertNotIn("text a", out)

    def test_unknown_heading_reports_and_exits_one(self):
        path = self.write("a.md", "# A\n\ntext\n")
        code, out, err = run_cli(["--heading", "Z", path])
        self.assertEqual(code, 1)
        self.assertIn("no heading matching", err)
        self.assertEqual(out, "")

    def test_toc_and_heading_are_mutually_exclusive(self):
        path = self.write("a.md", "# A\n")
        with self.assertRaises(SystemExit) as caught:
            run_cli(["--toc", "--heading", "A", path])
        self.assertEqual(caught.exception.code, 2)

    def test_ascii_flag_produces_ascii_output(self):
        path = self.write("a.md", "| a |\n|---|\n| 1 |\n")
        _, out, _ = run_cli(["--ascii", path])
        out.encode("ascii")

    def test_theme_flag_accepted(self):
        path = self.write("a.md", "# T\n")
        code, out, _ = run_cli(["--theme", "light", path], FORCE_COLOR="1")
        self.assertEqual(code, 0)
        self.assertIn(m.THEMES["light"]["h1"], out)

    def test_config_file_supplies_defaults(self):
        config_home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, config_home, True)
        os.makedirs(os.path.join(config_home, "mdcat"))
        with open(os.path.join(config_home, "mdcat", "config"), "w", encoding="utf-8") as handle:
            handle.write("width = 30\n")
        path = self.write("a.md", "word " * 60)
        _, out, _ = run_cli([path], XDG_CONFIG_HOME=config_home)
        self.assertTrue(all(len(line) <= 30 for line in out.splitlines()))

    def test_flag_overrides_the_config_file(self):
        config_home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, config_home, True)
        os.makedirs(os.path.join(config_home, "mdcat"))
        with open(os.path.join(config_home, "mdcat", "config"), "w", encoding="utf-8") as handle:
            handle.write("width = 30\n")
        path = self.write("a.md", "word " * 60)
        _, out, _ = run_cli(["--width", "50", path], XDG_CONFIG_HOME=config_home)
        self.assertTrue(max(len(line) for line in out.splitlines()) > 30)

    def test_invalid_utf8_is_replaced_not_fatal(self):
        path = os.path.join(self.dir, "bin.md")
        with open(path, "wb") as handle:
            handle.write(b"# Title\n\nbad \xff byte\n")
        code, out, _ = run_cli([path])
        self.assertEqual(code, 0)
        self.assertIn("Title", out)

    def test_empty_file_produces_no_output(self):
        path = self.write("empty.md", "")
        code, out, _ = run_cli([path])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_output_has_no_trailing_blank_lines(self):
        path = self.write("a.md", "# Title\n\nbody\n")
        _, out, _ = run_cli([path])
        self.assertFalse(out.endswith("\n\n"))

    def test_sample_document_renders_without_error(self):
        code, out, err = run_cli([SAMPLE])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("mdcat sample document", out)

    def test_sample_document_respects_the_width(self):
        _, out, _ = run_cli(["--width", "72", SAMPLE])
        over = [line for line in out.splitlines() if m._display_width(line) > 72]
        self.assertEqual(over, [])

    def test_sample_document_outline(self):
        _, out, _ = run_cli(["--toc", SAMPLE])
        self.assertIn("Tables", out)
        self.assertNotIn("shaded panel", out)


# ------------------------------------------------------------------
# Paging
# ------------------------------------------------------------------
class TestPaging(unittest.TestCase):
    def test_no_pager_when_output_is_not_a_terminal(self):
        out = io.StringIO()
        saved, sys.stdout = sys.stdout, out
        try:
            m._emit(["line"] * 500, False, False, {})
        finally:
            sys.stdout = saved
        self.assertIn("line", out.getvalue())

    def test_pager_command_is_split_with_quoting_rules(self):
        # Regression: a plain split mangled quoted pager commands.
        with cli_environment(PAGER=f'{sys.executable} -c "import sys; sys.stdin.read()"'):
            self.assertTrue(m._page("hello", {}))

    def test_missing_pager_reports_failure_so_the_caller_can_print(self):
        with cli_environment(PAGER="definitely-not-a-real-pager-binary"):
            self.assertFalse(m._page("hello", {}))

    def test_malformed_pager_command_is_rejected(self):
        with cli_environment(PAGER='unbalanced "quote'):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertFalse(m._page("hello", {}))

    def test_empty_output_emits_nothing(self):
        out = io.StringIO()
        saved, sys.stdout = sys.stdout, out
        try:
            m._emit([], True, True, {})
        finally:
            sys.stdout = saved
        self.assertEqual(out.getvalue(), "")


# ------------------------------------------------------------------
# Subprocess smoke tests
# ------------------------------------------------------------------
class TestProcessBehaviour(unittest.TestCase):
    def test_version(self):
        result = run_process(["--version"])
        self.assertEqual(result.returncode, 0)
        self.assertIn(m.VERSION, result.stdout)

    def test_help(self):
        result = run_process(["--help"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("--width", result.stdout)

    def test_no_arguments_and_piped_empty_input(self):
        result = run_process([], stdin="")
        self.assertEqual(result.returncode, 0)

    def test_renders_the_sample_document(self):
        result = run_process([SAMPLE])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertIn("sample document", result.stdout)

    def test_broken_pipe_produces_no_traceback(self):
        # Regression: piping into a consumer that exits early raised
        # BrokenPipeError and printed a traceback.
        big = "# Big\n\n" + "\n\n".join(f"paragraph {i} of text" for i in range(4000))
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as handle:
            handle.write(big)
            path = handle.name
        self.addCleanup(os.unlink, path)
        writer = subprocess.Popen([sys.executable, SCRIPT, path],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        reader = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdin.readline()"],
            stdin=writer.stdout, stdout=subprocess.DEVNULL)
        writer.stdout.close()
        reader.wait()
        stderr = writer.communicate()[1].decode()
        self.assertNotIn("Traceback", stderr)
        self.assertNotIn("BrokenPipeError", stderr)

    def test_ascii_output_encoding_does_not_crash(self):
        # Regression: box-drawing glyphs raised UnicodeEncodeError under a
        # C/POSIX locale.
        result = run_process([SAMPLE], env={"PYTHONIOENCODING": "ascii", "LC_ALL": "C"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        result.stdout.encode("ascii")

    def test_latin1_output_encoding_does_not_crash(self):
        result = run_process([SAMPLE], env={"PYTHONIOENCODING": "latin-1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_file_exit_code(self):
        result = run_process(["/nonexistent/path/file.md"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("mdcat:", result.stderr)

    @unittest.skipUnless(os.path.exists(os.path.join(HERE, "mdcat")), "launcher not present")
    def test_launcher_script_is_valid_shell(self):
        result = subprocess.run(["bash", "-n", os.path.join(HERE, "mdcat")],
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


# ------------------------------------------------------------------
# Static analysis
# ------------------------------------------------------------------
class TestStaticAnalysis(unittest.TestCase):
    def setUp(self):
        with open(SCRIPT, encoding="utf-8") as handle:
            self.source = handle.read()
        self.tree = ast.parse(self.source)

    def test_module_compiles(self):
        compile(self.source, SCRIPT, "exec")

    def test_every_module_level_function_is_documented(self):
        undocumented = [node.name for node in self.tree.body
                        if isinstance(node, ast.FunctionDef) and not ast.get_docstring(node)]
        self.assertEqual(undocumented, [])

    def test_no_wildcard_imports(self):
        starred = [node for node in ast.walk(self.tree)
                   if isinstance(node, ast.ImportFrom)
                   and any(alias.name == "*" for alias in node.names)]
        self.assertEqual(starred, [])

    def test_only_standard_library_imports(self):
        allowed = {"argparse", "errno", "os", "re", "shlex", "shutil",
                   "subprocess", "sys", "unicodedata", "dataclasses", "typing"}
        imported = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(imported - allowed, set())

    def test_pyflakes_reports_nothing(self):
        try:
            from pyflakes.api import check
            from pyflakes.reporter import Reporter
        except ImportError:
            self.skipTest("pyflakes is not installed")
        out, err = io.StringIO(), io.StringIO()
        warnings = check(self.source, SCRIPT, Reporter(out, err))
        self.assertEqual(warnings, 0, out.getvalue() + err.getvalue())


class FakeTerminal(io.StringIO):
    """A writable stream that claims to be an interactive terminal."""

    def isatty(self):
        return True


class TestEdgeCases(unittest.TestCase):
    def test_overlong_frontmatter_is_treated_as_content(self):
        body = "\n".join(f"k{i}: v" for i in range(m.MAX_FRONTMATTER_LINES + 5))
        parsed = blocks(f"---\n{body}\n---\n")
        self.assertIsInstance(parsed[0], m.Rule)

    def test_blank_line_inside_indented_code_is_kept(self):
        parsed = blocks("    a\n\n    b")
        self.assertIsInstance(parsed[0], m.CodeBlock)
        self.assertEqual(parsed[0].lines, ["a", "", "b"])

    def test_indented_code_ends_at_unindented_text(self):
        parsed = blocks("    a\ntext")
        self.assertIsInstance(parsed[0], m.CodeBlock)
        self.assertIsInstance(parsed[1], m.Para)

    def test_indented_code_ends_at_a_trailing_blank_line(self):
        parsed = blocks("    a\n\ntext")
        self.assertEqual(parsed[0].lines, ["a"])
        self.assertIsInstance(parsed[1], m.Para)

    def test_successive_notes_after_a_child_block_join_one_paragraph(self):
        items = blocks("- item\n  ```\n  code\n  ```\n  note one\n  note two")[0].items
        self.assertEqual(len(items[0].children), 2)
        self.assertEqual(items[0].children[1].text, "note one note two")

    def test_table_degrades_when_no_width_is_available(self):
        header = "| " + " | ".join(f"c{i}" for i in range(12)) + " |"
        sep = "|" + "---|" * 12
        lines = plain(f"{header}\n{sep}\n{header}", m.MIN_WIDTH)
        self.assertTrue(lines)  # renders something rather than raising

    def test_wide_table_trim_loop_respects_the_budget(self):
        # One very wide column beside several narrow ones: the per-column
        # floor pushes the total over budget, so the trim loop must run.
        chosen = m._table_col_widths(["a" * 30, "b", "c", "d"], [], 40, 4)
        self.assertLessEqual(sum(chosen), 40 - 5 - 8)

    def test_even_table_columns_fit_the_budget(self):
        chosen = m._table_col_widths(["a" * 10] * 5, [], 40, 5)
        self.assertLessEqual(sum(chosen), 40 - 6 - 10)

    def test_write_out_treats_epipe_as_a_broken_pipe(self):
        import errno as _errno

        class EpipeStream(io.StringIO):
            def write(self, _text):
                raise OSError(_errno.EPIPE, "Broken pipe")

        saved, sys.stdout = sys.stdout, EpipeStream()
        try:
            m._write_out("text")  # must not raise
        finally:
            sys.stdout = saved

    def test_discard_stdout_tolerates_a_stream_without_a_descriptor(self):
        saved, sys.stdout = sys.stdout, io.StringIO()
        try:
            m._discard_stdout()  # must not raise
        finally:
            sys.stdout = saved

    def test_write_out_survives_a_broken_pipe(self):
        class ClosedPipe(io.StringIO):
            def write(self, _text):
                raise BrokenPipeError(32, "Broken pipe")

        saved, sys.stdout = sys.stdout, ClosedPipe()
        try:
            m._write_out("text")  # must not raise
        finally:
            sys.stdout = saved

    def test_write_out_reraises_unrelated_os_errors(self):
        class BadStream(io.StringIO):
            def write(self, _text):
                raise OSError(errno_value, "disk failure")

        import errno as _errno
        errno_value = _errno.ENOSPC
        saved, sys.stdout = sys.stdout, BadStream()
        try:
            with self.assertRaises(OSError):
                m._write_out("text")
        finally:
            sys.stdout = saved

    def test_pager_with_an_empty_command_is_rejected(self):
        with cli_environment():
            self.assertFalse(m._page("hello", {"pager": "   "}))

    def test_emit_uses_the_pager_for_long_output_on_a_terminal(self):
        stream = FakeTerminal()
        saved, sys.stdout = sys.stdout, stream
        try:
            with cli_environment(PAGER=f"{sys.executable} -c pass", LINES="10"):
                m._emit(["line"] * 100, False, False, {})
        finally:
            sys.stdout = saved
        self.assertEqual(stream.getvalue(), "")  # the pager consumed it

    def test_help_is_shown_when_stdin_is_an_interactive_terminal(self):
        out, err = FakeTerminal(), io.StringIO()
        saved = (sys.stdout, sys.stderr, sys.stdin)
        with cli_environment():
            sys.stdout, sys.stderr, sys.stdin = out, err, FakeTerminal()
            try:
                code = m.main([])
            finally:
                sys.stdout, sys.stderr, sys.stdin = saved
        self.assertEqual(code, 1)
        self.assertIn("usage:", out.getvalue())

    def test_long_heading_is_wrapped(self):
        lines = plain("## " + "heading words " * 6, 30)
        self.assertTrue(all(w <= 30 for w in widths(lines)), widths(lines))

    def test_long_frontmatter_value_is_wrapped(self):
        lines = plain("---\ntitle: " + "value words " * 6 + "\n---\n", 30)
        self.assertTrue(all(w <= 30 for w in widths(lines)), widths(lines))

    def test_frontmatter_value_gets_a_hanging_indent(self):
        lines = plain("---\ntitle: " + "value words " * 6 + "\n---\n", 40)
        self.assertTrue(lines[2].startswith(" " * len("title: ")), repr(lines[2]))

    def test_overlong_frontmatter_key_still_fits(self):
        lines = plain("---\n" + "k" * 60 + ": v\n---\n", 30)
        self.assertTrue(all(w <= 30 for w in widths(lines)), widths(lines))


class TestWidthInvariant(unittest.TestCase):
    """No rendered line may ever exceed the requested width."""

    def check(self, source, width, **kwargs):
        lines = plain(source, width, **kwargs)
        over = [(w, line) for w, line in zip(widths(lines), lines) if w > width]
        self.assertEqual(over, [], f"width={width}")

    def test_sample_document_at_many_widths(self):
        with open(SAMPLE, encoding="utf-8") as handle:
            source = handle.read()
        for width in (20, 24, 31, 40, 55, 80, 120, 200):
            self.check(source, width)

    def test_sample_document_in_ascii_mode(self):
        with open(SAMPLE, encoding="utf-8") as handle:
            source = handle.read()
        for width in (20, 40, 80):
            self.check(source, width, ascii_only=True)

    def test_deeply_nested_content_at_the_minimum_width(self):
        source = ("- one\n  - two\n    - three\n      - four\n"
                  "        ```\n        nested code sample\n        ```\n")
        self.check(source, m.MIN_WIDTH)

    def test_quoted_list_with_code_at_the_minimum_width(self):
        self.check("> - item with a reasonably long body\n>\n> ```\n> code\n> ```\n", m.MIN_WIDTH)

    def test_wide_characters_at_the_minimum_width(self):
        self.check("\u65e5\u672c\u8a9e" * 20 + "\n\n| \u65e5\u672c | b |\n|---|---|\n| 1 | 2 |\n",
                   m.MIN_WIDTH)

    def test_module_runs_as_a_script(self):
        result = subprocess.run([sys.executable, SCRIPT, "--version"],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
