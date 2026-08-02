# mdcat

**Version 0.2**

Render a Markdown file to the terminal, styled close to what a proper Markdown
reader would show: coloured headers, bold and italic, tables, syntax-lit code
panels with line numbers, task lists, and more.

Zero dependencies — pure Python standard library. Nothing to install, no
network access needed, and the same behaviour on a laptop or over SSH on a
cluster login node.

```
mdcat README.md
mdcat --toc long-document.md
mdcat --heading "Configuration" notes.md
cat notes.md | mdcat --width 80
```

---

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Command-line reference](#command-line-reference)
- [Output reference](#output-reference) — what every construct looks like
- [Colour, width, paging and encoding](#colour-width-paging-and-encoding)
- [Configuration file](#configuration-file)
- [Exit codes](#exit-codes)
- [Supported Markdown](#supported-markdown)
- [Assumptions](#assumptions)
- [Known limitations](#known-limitations)
- [Project structure](#project-structure) — developer documentation begins here
- [Architecture](#architecture)
- [Testing](#testing)
- [Future work](#future-work)
- [Version history](#version-history)
- [License](#license)

---

## Requirements

| Requirement | Detail |
| :--- | :--- |
| Python | 3.7 or newer (uses `dataclasses`, standard library since 3.7) |
| Terminal | Any emulator understanding standard ANSI/SGR escape codes |
| Encoding | UTF-8 preferred; ASCII and other limited encodings are detected and handled |
| Pager | Optional. `less` or any `$PAGER` for auto-paging long output |
| Packages | None. Standard library only |

No third-party packages are used, and nothing is downloaded at install or run
time.

## Installation

### Offline install script (recommended)

```
./install.sh                 # installs into ~/.local/bin
./install.sh /usr/local/bin  # or a directory of choice
./install.sh --uninstall     # removes a previous installation
```

The script copies `mdcat.py` and the `mdcat` launcher into the target
directory, makes them executable, and warns if that directory is not on
`PATH`. It requires no network access and no package manager.

### Manual

```
chmod +x mdcat.py mdcat
cp mdcat.py mdcat ~/bin/
```

The `mdcat` launcher is a small shell wrapper that resolves symlinks and then
runs `mdcat.py` beside it, so `mdcat file.md` works without typing `python3`.
Alternatively, symlink or rename `mdcat.py` itself to `mdcat` on `PATH`, or
run it in place with `./mdcat.py file.md`.

### As a package

```
pip install .        # or: pipx install .
```

This installs an `mdcat` console script. Packaging metadata lives in
`pyproject.toml`; the project has no dependencies to resolve.

## Quick start

```
mdcat sample.md
```

`sample.md` ships with the project and exercises every supported construct, so
it doubles as a way to check how the rendering looks in a particular terminal.

```
mdcat README.md                        # render a file
mdcat notes.md other.md                # several files, each with a banner
cat notes.md | mdcat                   # read from standard input
mdcat --theme light README.md          # for a light-background terminal
mdcat --no-color README.md > plain.txt # strip styling, e.g. for grep or diff
mdcat --toc long-doc.md                # just the heading outline
mdcat --heading "Common mistakes" x.md # jump straight to one section
mdcat --width 80 README.md             # force a specific wrap width
mdcat --ascii README.md                # plain ASCII box drawing
```

## Command-line reference

```
mdcat [options] [file ...]
```

With no file argument, or with `-` as a filename, mdcat reads from standard
input. Several files may be given; each is rendered in turn with a banner
naming it.

| Option | Argument | Default | Description |
| :--- | :--- | :--- | :--- |
| `--width` | integer ≥ 20 | detected | Wrap width in columns. Values below 20 are rejected |
| `--theme` | `dark` \| `light` | `dark` | Colour set matched to the terminal background |
| `--no-color`, `--plain` | — | off | Disable all styling and emit plain text |
| `--ascii` | — | auto | Draw with ASCII characters instead of box-drawing glyphs |
| `--no-pager` | — | off | Never page output through `$PAGER`/`less` |
| `--toc` | — | off | Print only the heading outline. Mutually exclusive with `--heading` |
| `--heading` | name | — | Print only one section. Mutually exclusive with `--toc` |
| `--version` | — | — | Print the version and exit |
| `-h`, `--help` | — | — | Show help and exit |

`--heading` matches case-insensitively. An exact heading match is preferred; if
none is found, the first heading *containing* the given text is used, so a
partial title is enough. The section runs from that heading through to the next
heading of the same or a higher level.

---

## Output reference

Each construct below shows the rendered result followed by a breakdown of
every element in it. Mockups are shown with styling stripped, since colour
cannot be reproduced here; the breakdown states what colour each element
carries.

### Document view

The default view. Blocks are rendered in document order with one blank line
between them, wrapped to the terminal width.

### Headings

```
Project title
────────────────────────────────────────────────────────────────

Section
```

| Element | Rendering |
| :--- | :--- |
| Level 1 (`#`) | Bold, brightest heading colour, followed by a full-width rule |
| Level 2 (`##`) | Bold, second heading colour, no rule |
| Levels 3–6 | Bold, third heading colour, no rule |
| Heading text | Inline styling is applied, so `` `code` `` and `**bold**` render inside headings |
| Closing hashes | A trailing run of `#` preceded by whitespace is removed; `# C#` keeps its hash |

Heading level is *not* expressed through indentation in the document view —
only through colour and the level-1 rule. Use `--toc` for an indented outline.

### Paragraphs and inline styling

Paragraphs are reflowed to the wrap width, so source line breaks do not affect
the output.

| Markup | Rendering |
| :--- | :--- |
| `**bold**`, `__bold__` | Bold |
| `*italic*`, `_italic_` | Italic |
| `***both***`, `___both___` | Bold and italic |
| `` `code` `` | Inline-code colour on a shaded background |
| `==highlight==` | Dark text on a highlight background |
| `~~strike~~` | Strikethrough |
| `[label](url)` | Underlined coloured label, then the URL in parentheses in a muted colour |
| `<https://…>` | Underlined coloured link text |
| `![alt](url)` | `[image: alt]` placeholder in a muted colour; a terminal cannot draw the image |

Emphasis requires a non-word character on both sides of the marker. This is a
deliberate simplification: it keeps `snake_case_names` and expressions such as
`2**10` from being misread as emphasis, which matters far more in
code-heavy documents than mid-word emphasis does.

Words too long to fit the wrap width — a long URL, for instance — are broken
across lines rather than allowed to overflow.

### Lists

```
• top level item
  ◦ nested item
    • third level
1. first
  1. nested one
  2. nested two
2. second
☑ done
☐ pending
```

| Element | Rendering |
| :--- | :--- |
| Bulleted marker | `•` at even nesting levels, `◦` at odd levels, in the bullet colour |
| Ordered marker | `N. ` where N counts within its nesting level |
| Ordered start | An explicit first number is honoured, so a list starting at `5.` continues 5, 6, 7 |
| Nested numbering | Each sublist restarts at 1; leaving a level discards its counter |
| Task item | `☑` when checked, `☐` when not, replacing the bullet |
| Indentation | Two columns per nesting level, regardless of the source indent width |
| Wrapped text | Continuation lines are indented to align under the item text, not the marker |

Nesting levels are derived from an indentation stack rather than from a fixed
indent width, so a document mixing two-space and four-space indentation still
nests consistently.

A list item can carry a fenced code block, which is rendered as a panel
indented under the item:

```
• item
                                    (shaded panel begins)
  1 │ echo hi
                                    (shaded panel ends)
```

### Code panel

````
                                                  (shaded, full width)
1 │ def main():
2 │     return 0  # done
                                                  (shaded, full width)
````

| Element | Rendering |
| :--- | :--- |
| Panel background | A shaded background spanning the full wrap width, including a blank row above and below |
| Line numbers | Right-aligned in a muted colour, width set by the largest line number |
| Separator | `│` between the number and the code |
| Continuation rows | A line too wide for the panel wraps onto further rows marked `↳`, keeping the panel rectangular |
| Tabs | Expanded to four columns before measuring, so panel edges stay straight |
| Keywords | Bold, keyword colour — Python and Bash fences only |
| Strings | String colour — Python and Bash fences only |
| Comments | Dim comment colour from the first unquoted `#` to end of line |
| Other languages | Rendered plain; the panel, numbers and wrapping still apply |

Both fenced (`` ``` `` or `~~~`) and four-space-indented code blocks are
supported. A closing fence must use the same character as the opening one and
be at least as long, so a three-backtick fence nests correctly inside a
four-backtick block.

### Blockquote

```
│ A quoted paragraph, styled in italic.
```

| Element | Rendering |
| :--- | :--- |
| Bar | `│` in the quote colour at the start of every line, including wrapped ones |
| Single paragraph | Italic in the quote colour |
| Richer content | Lists, code blocks, several paragraphs and nested quotes are parsed and rendered normally beside the bar |
| Width | Content is laid out two columns narrower to leave room for the bar |

Every line of a quote needs its own `>`; lazy continuation (a wrapped line
that omits the marker) is not supported.

### Table

```
┌─────────┬─────────┬──────────┐
│ Option  │  Type   │  Default │
├─────────┼─────────┼──────────┤
│ --width │ integer │ detected │
│ --theme │ choice  │     dark │
└─────────┴─────────┴──────────┘
```

| Element | Rendering |
| :--- | :--- |
| Borders | Box-drawing characters in the border colour |
| Header row | Bold, separated from the body by a full-width divider |
| Alignment | `:---` left, `:---:` centre, `---:` right, taken from the separator row |
| Cell contents | Full inline styling; cells wrap onto multiple rows when needed |
| Column widths | Sized to content, then shrunk proportionally if the table would exceed the wrap width |
| Ragged rows | Column count follows the widest row; short rows are padded, surplus cells are kept |
| Wide characters | Measured by display columns, so CJK and emoji stay aligned |

Two forms of literal pipe are supported inside a cell: `\|` for an escaped
pipe, and a pipe inside an inline code span such as `` `cmd | filter` ``.
Neither splits the cell.

**Narrow terminals.** A grid needs one border per gap, a space of padding each
side of every cell, and at least one column of content per cell — so a table of
N columns cannot be drawn in fewer than `4N + 1` columns. Below that, the table
is listed as labelled records instead of being drawn as an overflowing grid:

```
c0: first value
c1: second value
c2: third value

c0: next row
c1: and so on
```

Each row becomes one record, records are separated by a blank line, and the
header cell supplies each label. Columns with an empty header are labelled
`column N`.

### Frontmatter panel

```
────────────────────────────────────────────────────────────────
title: Notes
version: 3
────────────────────────────────────────────────────────────────
```

| Element | Rendering |
| :--- | :--- |
| Rules | Full-width rules above and below the block |
| Key | Bold, key colour |
| Value | Value colour, after `: ` |
| Scope | A leading `---` block of flat `key: value` lines only. Blank lines and `#` comments inside it are ignored |

If the leading block does not parse as flat key/value pairs — because it
contains nested YAML, or runs longer than 200 lines — it is treated as
ordinary content, with the `---` becoming a horizontal rule.

### Horizontal rule

Three or more `-`, `*` or `_` characters draw a full-width rule in the rule
colour. A line such as `- - -` matches both a rule and a list marker; it is
resolved as a rule, matching CommonMark.

### File banner

Shown once per file when more than one file is rendered:

```
═══════════════════════════ notes.md ═══════════════════════════
```

| Element | Rendering |
| :--- | :--- |
| Name | Bold, centred, surrounded by single spaces |
| Fill | `═` in the rule colour, padding both sides to the full width |

A single file is rendered without a banner.

### Outline view (`--toc`)

```
• mdcat sample document
  • Text and emphasis
  • Lists
  • Code
  • Quotes
  • Tables
```

| Element | Rendering |
| :--- | :--- |
| Bullet | `•` in the bullet colour |
| Indentation | Two columns per heading level below the first |
| Text | Full inline styling, so code spans in headings still render |
| No headings | The single line `(no headings found)` |

### Section view (`--heading`)

Renders exactly the blocks of the matched section, starting with its heading
and ending before the next heading of the same or a higher level. Output is
otherwise identical to the document view. If no heading matches, a message is
written to standard error, nothing is written to standard output, and the exit
code is 1.

---

## Colour, width, paging and encoding

**Colour** is enabled when standard output is a terminal and disabled when it
is piped or redirected. `--no-color` / `--plain` always disables it, as does a
non-empty `NO_COLOR` environment variable. A non-empty `FORCE_COLOR` re-enables
it even when piping. `NO_COLOR` wins over `FORCE_COLOR`.

**Width** defaults to the terminal's current width, falling back to 100 columns
when it cannot be detected. The minimum accepted width is 20 columns.

**Paging** happens automatically when standard output is an interactive
terminal and the rendered output is taller than the terminal. The command is
taken from `$PAGER`, falling back to `less -R`. The command is split with
shell quoting rules, so a `$PAGER` containing quoted arguments works. If the
pager cannot be started, output is printed directly instead. `--no-pager` and
`pager = disabled` in the config file both switch paging off.

**Encoding** is checked before rendering. If standard output cannot represent
box-drawing characters — which is the case under a C or POSIX locale — an
ASCII glyph set is used automatically. `--ascii` forces this. Characters that
still cannot be encoded are replaced rather than raising an error.

**Broken pipes** are handled: piping into a consumer that exits early, such as
`mdcat big.md | head`, terminates quietly instead of printing a traceback.

**Input safety**: escape sequences and control characters in a source document
are stripped before parsing. A Markdown file is treated as data, so it cannot
drive the terminal, clear the screen, rewrite the window title, or forge styled
output.

Precedence for width, theme and paging, highest first:

**command-line flag > environment variable (`PAGER` only) > config file >
built-in default**

## Configuration file

Optional. Read from `$XDG_CONFIG_HOME/mdcat/config`, or
`~/.config/mdcat/config` when that variable is unset. Flat `key = value` lines;
`#` starts a comment; blank lines are ignored.

```
width = 100
theme = dark
pager = disabled
```

| Key | Accepted values | Effect |
| :--- | :--- | :--- |
| `width` | integer ≥ 20 | Default wrap width |
| `theme` | `dark`, `light` | Default colour theme |
| `pager` | `disabled`, or a pager command | `disabled` turns off auto-paging; any other value is used as the pager command when `$PAGER` is unset |

Invalid values are reported on standard error and ignored, and the built-in
default is used instead. A missing or unreadable config file is not an error.
Every setting is overridden by the matching command-line flag.

## Exit codes

| Code | Meaning |
| :--- | :--- |
| 0 | Everything rendered successfully |
| 1 | A file could not be read, a `--heading` did not match, or no input was given on an interactive terminal |
| 2 | Invalid command-line usage (rejected by the argument parser) |
| 130 | Interrupted with Ctrl-C |

When several files are given, unreadable ones are reported on standard error
and the rest are still rendered; the exit code is 1.

## Supported Markdown

**Block level:** headers (`#` through `######`), paragraphs reflowed to the
terminal width, nested bulleted and numbered lists, `- [ ]` / `- [x]` task
items, fenced (`` ``` `` / `~~~`) and four-space-indented code blocks, code
blocks nested inside list items, blockquotes (`>`) containing arbitrary nested
blocks, pipe tables with `:---` / `---:` / `:---:` alignment, horizontal rules
(`---`, `***`, `___`), and flat `key: value` frontmatter.

**Inline:** `**bold**`, `*italic*`, `***both***` and the `__` / `_`
equivalents, `` `inline code` ``, `==highlight==`, `~~strikethrough~~`,
`[links](url)`, `![images](url)` shown as an alt-text placeholder, and bare
`<https://…>` autolinks.

**Syntax highlighting:** Bash and Python fences receive keyword, string and
comment colouring. Other languages are rendered plain inside the same panel.

## Assumptions

- Input is Markdown text, not a program. Escape sequences in a document are
  removed rather than honoured.
- The terminal interprets standard ANSI/SGR codes and, unless the encoding says
  otherwise, can display box-drawing characters.
- Terminal cell width follows the Unicode East Asian Width property: wide and
  fullwidth characters occupy two columns, combining marks occupy none.
- A tab advances to the next four-column stop.
- Documents fit comfortably in memory; each file is read and rendered whole
  before any output is produced.

## Known limitations

This is a deliberately basic renderer, not a full CommonMark implementation.

- Mid-word emphasis (`feas*ible*`) is not supported: emphasis requires a
  non-word character on both sides. This is the trade that protects
  `snake_case_names` and `2**10`.
- Blockquotes do not support lazy continuation; every line of a quote needs its
  own `>`.
- Frontmatter parsing is flat `key: value` only — no nested YAML.
- Bash and Python highlighting is line-by-line and does not track state across
  lines, so a Python triple-quoted string spanning several lines is not
  highlighted as a single unit.
- Reference-style links (`[text][ref]`), footnotes, definition lists and setext
  headings (`Title` underlined with `===`) are not supported. A setext-style
  underline renders as a horizontal rule.
- Embedded HTML is not supported, by design.
- Two lists separated only by a blank line are treated as one list, so
  numbering continues across the gap.
- A table too narrow to draw as a grid is listed as labelled records rather
  than rendered as a table. This is a deliberate fallback, but it does mean a
  wide table looks quite different on a narrow terminal.
- Below 20 columns the layout stops being meaningful for any construct, which
  is why that is the enforced minimum width.

---

## Project structure

```
mdcat.py                the tool itself (single file, standard library only)
mdcat                   launcher shim -- lets `mdcat file.md` work directly
install.sh              offline installer / uninstaller
test_mdcat.py           test suite (219 tests)
sample.md               demo document and integration-test fixture
pyproject.toml          packaging metadata (optional pip/pipx install)
.github/workflows/ci.yml  test matrix across Python 3.7-3.13
README.md               this file
LICENSE                 GPL-3.0
```

The implementation is kept in a single source file on purpose. At roughly 1,300
lines including comments it remains easy to read end to end, and a single file
is trivial to copy onto a remote machine, which is a common way the tool is
deployed.

## Architecture

Three stages, each taking plain data in and returning plain data out:

```
raw text ──▶ _prepare_lines() ──▶ parse_blocks() ──▶ render_blocks() ──▶ _emit()
             sanitise input        text ─▶ AST        AST ─▶ ANSI lines   page/print
```

1. **`_prepare_lines(text)`** expands tabs to a fixed stop, strips escape
   sequences and control characters, and splits into lines. Everything
   downstream can then assume clean text containing only spaces for
   indentation. This is also where the input-safety guarantee is enforced.

2. **`parse_blocks(lines)`** produces a flat list of block objects. Each block
   type is a small dataclass: `Heading`, `Para`, `ListBlock` (holding
   `ListItem`s), `CodeBlock`, `Quote`, `Table`, `Rule`, `FrontMatter`. Two node
   types nest: a `ListItem` carries a `children` list of blocks, and a `Quote`
   carries a `blocks` list, both parsed recursively. Block-level parsing never
   touches inline markup.

3. **`render_blocks(blocks, theme, width)`** turns the AST into terminal lines.
   Inline markup is handled here by `render_inline()`, which protects code
   spans behind placeholders, then substitutes images, autolinks, links and
   emphasis in that order, then restores the code spans. Wrapping is done by
   `_wrap_styled()`, which measures *display columns* rather than characters.

4. **`_emit(lines, …)`** decides between the pager and direct output, strips
   styling when colour is off, and tolerates a closed pipe.

Some design points worth knowing before changing anything:

- **Attribute-specific unsets, not blanket resets.** Closing an inline span
  emits the specific unset code (22/23/24/29/39/49) rather than `\x1b[0m`, so a
  span nested inside another does not cancel the outer style when it ends. A
  full reset appears only at the end of a complete rendered line. The one
  asymmetry is SGR 22, which clears bold *and* dim because the terminal spec
  provides no separate code, so any style that may appear nested inside bold
  uses colour rather than dim.

- **Display width, not string length.** `_display_width()` consults the Unicode
  East Asian Width property and combining-character class. Every alignment
  computation — wrapping, table columns, code-panel padding, list indents, the
  file banner — goes through `_visible_len()`, which strips escapes and then
  measures columns. Using `len()` anywhere in layout code is a bug.

- **Background repair across wraps.** Foreground attributes survive a newline
  harmlessly, but an open background paints the rest of the terminal row.
  `_reflow_backgrounds()` tracks the active background through a line's escape
  sequences, closes it at the line end and reopens it on the next.

- **Theme carries glyphs.** `build_theme(name, ascii_only)` merges a colour set
  with a glyph set, so the drawing-character choice travels with the theme
  through every renderer instead of living in a global.

- **Placeholder characters.** Code spans are stashed behind NUL markers and
  code strings behind SOH markers during substitution. Both characters are
  removed by input sanitisation, which is what makes them safe to use.

## Testing

```
python3 -m unittest test_mdcat -v
```

The suite has **219 tests** and takes about one second. It is organised as:

| Group | What it covers |
| :--- | :--- |
| Unit — sanitisation | Tab expansion, escape and control-character removal |
| Unit — measurement | Display width, wide characters, combining marks, chunking |
| Unit — parsing | Headings, paragraphs, code blocks, lists, quotes, tables, frontmatter, rules |
| Unit — inline | Emphasis forms, code spans, links, images, autolinks, style nesting |
| Unit — wrapping | Word wrap, long-word breaking, background repair across wraps |
| Unit — highlighting | Keywords, strings, comments, unknown languages |
| Unit — config | File parsing, value validation, warnings |
| Integration — CLI | `main()` driven in-process with captured streams: every flag, multi-file handling, error paths, config precedence |
| Smoke — subprocess | Real process behaviour: exit codes, broken pipes, restricted output encodings, launcher syntax |
| Static analysis | Module compiles, every module-level function documented, no wildcard imports, standard-library imports only, pyflakes clean |
| Regression | One named test per defect fixed in 0.2 |
| Invariant | No rendered line exceeds the requested width, checked over the sample document at eight widths, in ASCII mode, and on deeply nested and wide-character content |

### Coverage

Measured with `coverage` in branch mode:

```
python3 -m coverage run --branch -m unittest test_mdcat
python3 -m coverage report -m --include=mdcat.py
```

| Metric | Result |
| :--- | :--- |
| Statements | 789 |
| Line coverage | **98.9%** (780 of 789) |
| Branches | 258 |
| Branch coverage | **97.3%** (251 of 258) |
| Combined | 98% |

The uncovered remainder is error handling that cannot be provoked reliably in
a test: a pager process failing mid-write, the `__main__` guard, and two
defensive branches in the table layout. CI enforces a 95% floor.

The static-analysis group runs pyflakes when it is installed and skips
otherwise, so the suite has no hard dependencies. To run everything including
the optional check:

```
pip install coverage pyflakes
```

## Future work

- Reference-style links and footnotes.
- Multi-line-aware syntax highlighting, so triple-quoted strings and here-docs
  colour correctly as one unit.
- Highlighting for more languages, ideally table-driven rather than one
  keyword set per language in code.
- Setext heading support (`Title` underlined with `===` or `---`).
- Optional heading numbering in the outline view.
- A `--follow` mode that re-renders when the file changes.
- A man page generated from the argument parser.

## Version history

### 0.2

Correctness and robustness release. Every item below is covered by a named
regression test.

**Crashes fixed**

- Piping into a consumer that exits early (`mdcat big.md | head`) raised
  `BrokenPipeError` and printed a traceback. Output now terminates quietly.
- Rendering under a C/POSIX locale raised `UnicodeEncodeError` on the first
  box-drawing character. The output encoding is now probed, an ASCII glyph set
  is selected automatically when needed, and unencodable characters are
  replaced rather than fatal.
- Ctrl-C produced a traceback; it now exits with code 130.
- An unreadable configuration file raised an uncaught `OSError`.

**Content no longer lost or mangled**

- A fenced code block inside a list item was absorbed into the item's text,
  destroying the sample. List items now carry nested blocks.
- Nested ordered-list counters never reset, so a second sublist continued the
  previous one's numbering.
- All layout used string length rather than display width, so CJK text, emoji
  and combining marks misaligned tables, wrapping and code panels.
- Tabs inside code blocks left the shaded panel ragged.
- A literal pipe in a table cell was impossible: `\|` kept its backslash and a
  pipe inside a code span split the cell. Both now work.
- Surplus cells in a ragged table row were silently dropped.
- A trailing `#` that was part of the heading text was stripped, so `# C#`
  rendered as `C`.
- Links and images closed with a blanket reset, cancelling any surrounding
  style; a link inside bold text unbolded the rest of the line.
- An inline code span that wrapped left its background open, shading the rest
  of the terminal row.
- Wrapped blockquote lines lost the quote styling.
- Blockquotes flattened nested lists and code blocks onto a single line; they
  now parse their contents as blocks.
- Four-space-indented code blocks were documented but not implemented.
- Overlong words (long URLs) overflowed the wrap width instead of breaking.
- Long headings and long frontmatter values were never wrapped and ran past the
  right edge at narrow widths.
- Layout floors for nested content were larger than the indent they sat inside,
  so deeply nested lists and code panels overflowed at narrow widths.
- A table with more columns than the width could hold overflowed at any width;
  it now falls back to a labelled-record listing.

**Behaviour and interface**

- Input is sanitised: escape sequences and control characters in a document are
  stripped, so a file cannot drive the terminal. Previously `--no-color` also
  failed to strip OSC and non-SGR CSI sequences.
- `--width 0` was silently ignored and negative widths were accepted; widths
  below 20 are now rejected with a message.
- `--toc` silently overrode `--heading`; they are now mutually exclusive.
- `$PAGER` was split on spaces, mangling quoted commands; it is now split with
  shell quoting rules.
- Invalid config values were ignored silently; they are now reported.
- A run that rendered nothing still printed a blank line.
- Added `--ascii` to force the ASCII glyph set.
- Table columns no longer overflow the requested width at narrow settings.
- Removed one unreachable exception handler.

**Project**

- Test suite expanded from 63 to 219 tests; coverage from 72% to 98%.
- Two tests depended on a fixture that was never committed, so a fresh clone
  could not pass the suite. `sample.md` now ships with the project.
- Added `install.sh`, `pyproject.toml`, `.gitignore`, and a CI workflow
  covering Python 3.7 through 3.13.
- Executable permissions are now set on the runtime files.

### 0.1

Initial release.

## License

GPL-3.0. See `LICENSE`.
