#!/usr/bin/env python3
"""Test suite for mdcat.py. Run with: python3 -m unittest test_mdcat -v"""
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mdcat as m

HERE = os.path.dirname(os.path.abspath(__file__))


def plain(text, width=100, theme="dark"):
    """Render text -> plain (ANSI-stripped) lines, for content assertions."""
    blocks = m.parse_blocks(text.splitlines())
    lines = m.render_blocks(blocks, m.THEMES[theme], width)
    return [m._strip_ansi(l) for l in lines]


def colored(text, width=100, theme="dark"):
    blocks = m.parse_blocks(text.splitlines())
    return m.render_blocks(blocks, m.THEMES[theme], width)


class TestHeadings(unittest.TestCase):
    def test_levels(self):
        blocks = m.parse_blocks(["# H1", "## H2", "###### H6"])
        self.assertEqual([b.level for b in blocks], [1, 2, 6])
        self.assertEqual([b.text for b in blocks], ["H1", "H2", "H6"])

    def test_trailing_hashes_stripped(self):
        blocks = m.parse_blocks(["## Title ##"])
        self.assertEqual(blocks[0].text, "Title")

    def test_inline_code_in_heading(self):
        out = plain("# `mb` guide\n")
        self.assertIn("mb guide", out[0])


class TestParagraphs(unittest.TestCase):
    def test_reflow_wraps_lines_together(self):
        blocks = m.parse_blocks(["This is one", "paragraph split", "across lines."])
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].text, "This is one paragraph split across lines.")

    def test_blank_line_separates_paragraphs(self):
        blocks = m.parse_blocks(["First para.", "", "Second para."])
        self.assertEqual(len(blocks), 2)


class TestCodeBlocks(unittest.TestCase):
    def test_basic_fence(self):
        blocks = m.parse_blocks(["```python", "x = 1", "```"])
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].lang, "python")
        self.assertEqual(blocks[0].lines, ["x = 1"])

    def test_fence_length_awareness(self):
        # a 3-backtick line nested inside a 4-backtick block must NOT close it
        src = ["````", "## not a heading", "```", "still inside", "```", "````"]
        blocks = m.parse_blocks(src)
        self.assertEqual(len(blocks), 1)
        self.assertIsInstance(blocks[0], m.CodeBlock)
        self.assertEqual(blocks[0].lines, ["## not a heading", "```", "still inside", "```"])

    def test_unterminated_fence_does_not_crash(self):
        blocks = m.parse_blocks(["```python", "x = 1"])
        self.assertEqual(blocks[0].lines, ["x = 1"])

    def test_tilde_fence(self):
        blocks = m.parse_blocks(["~~~", "code", "~~~"])
        self.assertEqual(blocks[0].lines, ["code"])

    def test_real_uploaded_file_nested_fence(self):
        with open(os.path.join(HERE, "ENTRY_TEMPLATE.md"), encoding="utf-8") as f:
            lines = f.read().splitlines()
        blocks = m.parse_blocks(lines)
        code_blocks = [b for b in blocks if isinstance(b, m.CodeBlock)]
        # the concept-type worked example is one 28-line block, not several
        # fragments split at the nested (shorter) fence markers
        big = [b for b in code_blocks if len(b.lines) >= 25]
        self.assertTrue(big, "expected at least one large (fence-nested) code block")
        self.assertTrue(any("## Definition" in l for l in big[0].lines))


class TestLists(unittest.TestCase):
    def test_nesting_levels(self):
        src = ["- top", "  - nested", "- top2"]
        blocks = m.parse_blocks(src)
        items = blocks[0].items
        self.assertEqual([i.level for i in items], [0, 1, 0])

    def test_ordered_renumbers_sequentially(self):
        src = ["1. a", "2. b", "5. c"]
        items = m.parse_blocks(src)[0].items
        self.assertEqual([i.number for i in items], [1, 2, 3])

    def test_ordered_respects_custom_start(self):
        items = m.parse_blocks(["5. a", "6. b"])[0].items
        self.assertEqual([i.number for i in items], [5, 6])

    def test_task_checkboxes(self):
        items = m.parse_blocks(["- [ ] todo", "- [x] done", "- [X] also done"])[0].items
        self.assertEqual([i.checked for i in items], [False, True, True])

    def test_wrapped_continuation_line_joins_item(self):
        src = ["- first line", "  continues here"]
        items = m.parse_blocks(src)[0].items
        self.assertEqual(items[0].text, "first line continues here")

    def test_hr_wins_over_ambiguous_dash_list(self):
        blocks = m.parse_blocks(["- - -"])
        self.assertIsInstance(blocks[0], m.Rule)

    def test_real_list_does_not_become_hr(self):
        blocks = m.parse_blocks(["- a real bullet"])
        self.assertIsInstance(blocks[0], m.ListBlock)


class TestBlockquote(unittest.TestCase):
    def test_joins_lines(self):
        blocks = m.parse_blocks(["> line one", "> line two"])
        self.assertIsInstance(blocks[0], m.Quote)
        self.assertEqual(blocks[0].text, "line one line two")


class TestTable(unittest.TestCase):
    def test_parses_header_aligns_rows(self):
        src = ["| A | B |", "|:--|--:|", "| 1 | 2 |"]
        t = m.parse_blocks(src)[0]
        self.assertEqual(t.header, ["A", "B"])
        self.assertEqual(t.aligns, ["l", "r"])
        self.assertEqual(t.rows, [["1", "2"]])

    def test_center_align_detected(self):
        t = m.parse_blocks(["|A|", "|:-:|"])[0]
        self.assertEqual(t.aligns, ["c"])

    def test_render_has_box_chars_and_content(self):
        out = plain("| Name | Qty |\n|:---|---:|\n| Widget | 3 |\n")
        joined = "\n".join(out)
        self.assertIn("Name", joined)
        self.assertIn("Widget", joined)
        self.assertIn("\u2502", joined)  # vertical border


class TestFrontMatter(unittest.TestCase):
    def test_parses_flat_pairs(self):
        src = ["---", "title: foo", "type: concept", "---", "", "# body"]
        blocks = m.parse_blocks(src)
        self.assertIsInstance(blocks[0], m.FrontMatter)
        self.assertEqual(blocks[0].pairs, [("title", "foo"), ("type", "concept")])

    def test_no_closing_fence_falls_back(self):
        src = ["---", "not really frontmatter"]
        blocks = m.parse_blocks(src)
        self.assertNotIsInstance(blocks[0], m.FrontMatter)

    def test_not_first_line_is_not_frontmatter(self):
        src = ["some text", "---", "title: foo", "---"]
        blocks = m.parse_blocks(src)
        self.assertNotIsInstance(blocks[0], m.FrontMatter)


class TestInlineEmphasis(unittest.TestCase):
    def test_bold_and_italic(self):
        out = m.render_inline("**bold** and *italic* and ***both***", m.THEMES["dark"])
        self.assertIn("\x1b[1mbold\x1b[22m", out)
        self.assertIn("\x1b[3mitalic\x1b[23m", out)
        self.assertIn("\x1b[1;3mboth\x1b[22;23m", out)

    def test_snake_case_not_italicized(self):
        out = m.render_inline("a snake_case_word here", m.THEMES["dark"])
        self.assertEqual(out, "a snake_case_word here")

    def test_exponent_not_bolded(self):
        out = m.render_inline("compute 2**10 now", m.THEMES["dark"])
        self.assertEqual(out, "compute 2**10 now")

    def test_real_underscore_italic_still_works(self):
        out = m.render_inline("some _italic_ word", m.THEMES["dark"])
        self.assertIn("\x1b[3mitalic\x1b[23m", out)

    def test_bold_around_code_span_nests_correctly(self):
        out = m.render_inline("**`code`**", m.THEMES["dark"])
        # closing the inner code span must not clobber the outer bold
        self.assertTrue(out.startswith("\x1b[1m"))
        self.assertTrue(out.endswith("\x1b[22m"))
        self.assertIn(m.THEMES["dark"]["code_inline"], out)

    def test_strike_and_highlight(self):
        out = m.render_inline("~~gone~~ and ==hi==", m.THEMES["dark"])
        self.assertIn("gone", out)
        self.assertIn("hi", out)


class TestCodeSpans(unittest.TestCase):
    def test_simple_span(self):
        out = m.render_inline("use `code` here", m.THEMES["dark"])
        self.assertIn("code", out)
        self.assertNotIn("`", out)

    def test_double_backtick_escapes_literal_backtick(self):
        # `` `code` `` should show the literal text `code` (with backticks)
        out = m.render_inline("inline `` `code` ``", m.THEMES["dark"])
        plain_out = m._strip_ansi(out)
        self.assertIn("`code`", plain_out)

    def test_single_backtick_wraps_triple_backtick_literal(self):
        out = m.render_inline("fenced ` ``` ` blocks", m.THEMES["dark"])
        plain_out = m._strip_ansi(out)
        self.assertIn("```", plain_out)

    def test_code_span_content_not_further_processed(self):
        out = m.render_inline("`**not bold**`", m.THEMES["dark"])
        plain_out = m._strip_ansi(out)
        self.assertEqual(plain_out, "**not bold**")


class TestLinksImages(unittest.TestCase):
    def test_link_renders_text_and_url(self):
        out = m._strip_ansi(m.render_inline("[click here](http://x.com)", m.THEMES["dark"]))
        self.assertIn("click here", out)
        self.assertIn("(http://x.com)", out)

    def test_image_placeholder(self):
        out = m._strip_ansi(m.render_inline("![a cat](cat.png)", m.THEMES["dark"]))
        self.assertIn("a cat", out)
        self.assertNotIn(".png", out)  # images show alt text only, url is omitted

    def test_autolink(self):
        out = m._strip_ansi(m.render_inline("<https://x.com>", m.THEMES["dark"]))
        self.assertIn("https://x.com", out)

    def test_emphasis_inside_link_label_renders(self):
        out = m.render_inline("[**bold link**](u)", m.THEMES["dark"])
        self.assertIn("\x1b[1mbold link\x1b[22m", out)


class TestSyntaxHighlight(unittest.TestCase):
    def test_python_keyword_highlighted(self):
        out = m._highlight_code_line("def f(x):", "python", m.THEMES["dark"])
        self.assertIn(m.THEMES["dark"]["code_keyword"], out)

    def test_bash_keyword_highlighted(self):
        out = m._highlight_code_line("if true; then", "bash", m.THEMES["dark"])
        self.assertIn(m.THEMES["dark"]["code_keyword"], out)

    def test_hash_in_string_not_treated_as_comment(self):
        out = m._highlight_code_line('echo "a # b"  # real', "bash", m.THEMES["dark"])
        plain_out = m._strip_ansi(out)
        self.assertEqual(plain_out, 'echo "a # b"  # real')
        self.assertIn(m.THEMES["dark"]["code_comment"], out)
        self.assertIn(m.THEMES["dark"]["code_string"], out)

    def test_unknown_language_left_plain(self):
        out = m._highlight_code_line("x = 1  # comment", "rust", m.THEMES["dark"])
        self.assertEqual(out, "x = 1  # comment")

    def test_line_numbers_present(self):
        block = m.CodeBlock("python", ["a", "b", "c"])
        out = "\n".join(m._render_code(block, m.THEMES["dark"], 60))
        plain_out = m._strip_ansi(out)
        self.assertIn("1 \u2502 a", plain_out)
        self.assertIn("3 \u2502 c", plain_out)


class TestWrapStyled(unittest.TestCase):
    def test_plain_wrap(self):
        lines = m._wrap_styled("one two three four five", 11)
        self.assertTrue(all(len(l) <= 11 for l in lines))
        self.assertEqual(" ".join(lines), "one two three four five")

    def test_styled_wrap_preserves_visible_content(self):
        styled = m.render_inline("a **bold** word and more text to wrap", m.THEMES["dark"])
        lines = m._wrap_styled(styled, 15)
        plain_out = m._strip_ansi(" ".join(lines))
        self.assertEqual(plain_out, "a bold word and more text to wrap")


class TestTOCAndHeading(unittest.TestCase):
    def test_toc_lists_headings_only(self):
        blocks = m.parse_blocks(["# A", "text", "## B"])
        toc = m._strip_ansi("\n".join(m.render_toc(blocks, m.THEMES["dark"])))
        self.assertIn("A", toc)
        self.assertIn("B", toc)
        self.assertNotIn("text", toc)

    def test_extract_section_stops_at_same_level(self):
        blocks = m.parse_blocks(["# T", "## One", "a", "## Two", "b"])
        section = m.extract_section(blocks, "One")
        texts = [b.text for b in section if isinstance(b, m.Para)]
        self.assertEqual(texts, ["a"])

    def test_extract_section_not_found(self):
        blocks = m.parse_blocks(["# T", "## One"])
        self.assertIsNone(m.extract_section(blocks, "Missing"))

    def test_extract_section_substring_match(self):
        blocks = m.parse_blocks(["## Common mistakes", "x"])
        section = m.extract_section(blocks, "mistakes")
        self.assertIsNotNone(section)


class TestCLI(unittest.TestCase):
    def _run(self, args, stdin=None, env=None):
        cmd = [sys.executable, os.path.join(HERE, "mdcat.py")] + args
        full_env = dict(os.environ, **(env or {}))
        return subprocess.run(cmd, input=stdin, capture_output=True, text=True, env=full_env)

    def test_missing_file_reports_error_and_exit_1(self):
        r = self._run(["/no/such/file.md"])
        self.assertEqual(r.returncode, 1)
        self.assertIn("no/such/file.md", r.stderr)

    def test_stdin_input(self):
        r = self._run(["--no-color"], stdin="# Hi\n")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Hi", r.stdout)

    def test_no_color_flag_strips_ansi(self):
        r = self._run(["--no-color"], stdin="**bold**\n")
        self.assertNotIn("\x1b[", r.stdout)

    def test_no_color_env_var(self):
        r = self._run([], stdin="**bold**\n", env={"NO_COLOR": "1"})
        self.assertNotIn("\x1b[", r.stdout)

    def test_force_color_env_var_enables_when_piped(self):
        r = self._run([], stdin="**bold**\n", env={"FORCE_COLOR": "1"})
        self.assertIn("\x1b[", r.stdout)

    def test_toc_flag(self):
        r = self._run(["--no-color", "--toc"], stdin="# A\ntext\n## B\n")
        self.assertIn("A", r.stdout)
        self.assertIn("B", r.stdout)
        self.assertNotIn("text", r.stdout)

    def test_heading_flag_not_found_errors(self):
        r = self._run(["--heading", "Nope"], stdin="# A\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no heading matching", r.stderr)

    def test_width_flag(self):
        r = self._run(["--no-color", "--width", "20"], stdin="one two three four five six seven\n")
        self.assertTrue(all(len(l) <= 20 for l in r.stdout.splitlines() if l))

    def test_multi_file_headers(self):
        a = os.path.join(HERE, "_ta.md")
        b = os.path.join(HERE, "_tb.md")
        with open(a, "w") as f:
            f.write("first file\n")
        with open(b, "w") as f:
            f.write("second file\n")
        try:
            r = self._run(["--no-color", a, b])
            self.assertIn("_ta.md", r.stdout)
            self.assertIn("_tb.md", r.stdout)
            self.assertIn("first file", r.stdout)
            self.assertIn("second file", r.stdout)
        finally:
            os.remove(a)
            os.remove(b)

    def test_config_file_sets_width(self):
        cfg_home = os.path.join(HERE, "_xdg_test")
        cfg_dir = os.path.join(cfg_home, "mdcat")
        os.makedirs(cfg_dir, exist_ok=True)
        with open(os.path.join(cfg_dir, "config"), "w") as f:
            f.write("# comment\nwidth = 12\ntheme = light\n")
        try:
            r = self._run(["--no-color"], stdin="one two three four five six\n",
                           env={"XDG_CONFIG_HOME": cfg_home})
            self.assertTrue(all(len(l) <= 12 for l in r.stdout.splitlines() if l))
        finally:
            os.remove(os.path.join(cfg_dir, "config"))
            os.rmdir(cfg_dir)
            os.rmdir(cfg_home)

    def test_config_file_sets_theme(self):
        cfg_home = os.path.join(HERE, "_xdg_test3")
        cfg_dir = os.path.join(cfg_home, "mdcat")
        os.makedirs(cfg_dir, exist_ok=True)
        with open(os.path.join(cfg_dir, "config"), "w") as f:
            f.write("theme = light\n")
        try:
            r = self._run([], stdin="# H\n", env={"XDG_CONFIG_HOME": cfg_home, "FORCE_COLOR": "1"})
            self.assertIn(m.THEMES["light"]["h1"], r.stdout)
        finally:
            os.remove(os.path.join(cfg_dir, "config"))
            os.rmdir(cfg_dir)
            os.rmdir(cfg_home)

    def test_cli_flag_overrides_config_file(self):
        cfg_home = os.path.join(HERE, "_xdg_test2")
        cfg_dir = os.path.join(cfg_home, "mdcat")
        os.makedirs(cfg_dir, exist_ok=True)
        with open(os.path.join(cfg_dir, "config"), "w") as f:
            f.write("theme = light\n")
        try:
            r = self._run(["--theme", "dark"], stdin="# H\n",
                           env={"XDG_CONFIG_HOME": cfg_home, "FORCE_COLOR": "1"})
            self.assertIn(m.THEMES["dark"]["h1"], r.stdout)
            self.assertNotIn(m.THEMES["light"]["h1"], r.stdout)
        finally:
            os.remove(os.path.join(cfg_dir, "config"))
            os.rmdir(cfg_dir)
            os.rmdir(cfg_home)

    def test_version_flag(self):
        r = self._run(["--version"])
        self.assertEqual(r.returncode, 0)
        self.assertIn(m.VERSION, r.stdout)

    def test_real_uploaded_file_runs_clean(self):
        r = self._run([os.path.join(HERE, "ENTRY_TEMPLATE.md")])
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr, "")


if __name__ == "__main__":
    unittest.main()
