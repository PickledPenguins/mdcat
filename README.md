# mdcat

Render a Markdown file to the terminal, styled close to what you'd see in a
proper Markdown reader: colored headers, bold/italic, tables, syntax-lit
code blocks with line numbers, task lists, and more.

Zero dependencies — pure Python standard library. Nothing to install, no
internet access needed, works the same on your laptop or over SSH on an
HPC login node.

## Requirements

- Python 3.7 or newer (uses `dataclasses`, in the standard library since 3.7)
- A terminal that understands standard ANSI/SGR escape codes (xterm and
  effectively every modern terminal emulator does)
- `less` on PATH if you want long files auto-paged (optional — falls back
  to a plain `print` if `less`/`$PAGER` isn't available)

## Install

```bash
chmod +x mdcat.py mdcat
```

Then either:

- Put both files on your `PATH` (e.g. `~/bin`) and call it as `mdcat file.md`
  — the `mdcat` launcher is a tiny shell wrapper so you never have to type
  `python3` yourself.
- Or symlink/rename just `mdcat.py` to `mdcat` somewhere on your `PATH`.
- Or run it in place: `./mdcat file.md` / `python3 mdcat.py file.md`.

## Usage

```
mdcat [options] [file ...]
```

With no file argument, or `-` as a filename, mdcat reads from stdin.

| Option | Description |
|---|---|
| `--width N` | Wrap width in columns (default: detect terminal width) |
| `--theme {dark,light}` | Color theme for your terminal background (default: `dark`) |
| `--no-color`, `--plain` | Disable all styling, print plain text |
| `--no-pager` | Don't page long output through `$PAGER`/`less` |
| `--toc` | Print only the heading outline |
| `--heading NAME` | Print only the section starting at this heading, through the next heading of the same or higher level |
| `--version` | Print the version and exit |
| `-h`, `--help` | Show help and exit |

### Examples

```bash
mdcat README.md                       # render a file
mdcat notes.md other.md                # multiple files, each with a filename header
cat notes.md | mdcat                   # read from stdin
mdcat --theme light README.md          # for a light-background terminal
mdcat --no-color README.md > plain.txt # strip styling, e.g. for grep/diff
mdcat --toc long-doc.md                # just the heading outline
mdcat --heading "Common mistakes" x.md # jump straight to one section
mdcat --width 80 README.md             # force a specific wrap width
```

Sample `--toc` output:

```
• Ground rules
• Formatting: for a basic renderer, not zero and not full CommonMark
• Frontmatter
• Concept-type layout
  • Worked example: concept entry
• Before you submit
```

## Supported Markdown

**Block-level:** headers (`#` through `######`), paragraphs (reflowed to
the terminal width), nested bulleted/numbered lists, `- [ ]`/`- [x]` task
items, fenced (` ``` `/`~~~`) and blockquote (`>`) blocks, pipe tables
with `:---`/`---:`/`:---:` alignment, horizontal rules (`---`, `***`,
`___`), and flat `key: value` frontmatter (a leading `---`/`---` block).

**Inline:** `**bold**`, `*italic*`, `***both***` (and the `__`/`_`
equivalents), `` `inline code` ``, `==highlight==`, `~~strikethrough~~`,
`[links](url)`, `![images](url)` (shown as an alt-text placeholder, since
a terminal can't display an actual image), and bare `<https://...>`
autolinks.

**Code blocks** get a shaded background and line numbers. Bash and Python
fences additionally get light keyword/string/comment highlighting.

## Color, width, and paging defaults

- Color is on when stdout is a terminal, off when it's piped/redirected.
  `--no-color`/`--plain` always disables it; the `NO_COLOR` environment
  variable does too. `FORCE_COLOR` re-enables it even when piping.
- Width defaults to your terminal's current width (or 100 columns if it
  can't be detected, e.g. when piped).
- Long output auto-pages through `$PAGER` (or `less -R` if `$PAGER` isn't
  set) when stdout is an interactive terminal and the content is taller
  than it. `--no-pager` always disables this.

Precedence for width/theme/pager, high to low: **command-line flag >
environment variable (`PAGER` only) > config file > built-in default.**

## Config file

Optional. Lives at `$XDG_CONFIG_HOME/mdcat/config`, or `~/.config/mdcat/config`
if that variable isn't set. Flat `key = value` lines, `#` comments allowed:

```
width = 100
theme = dark
pager = disabled
```

`pager = disabled` turns off auto-paging the same way `--no-pager` does.
Any setting here is overridden by the matching command-line flag.

## File structure

```
mdcat.py        the tool itself (single file, stdlib only)
mdcat           launcher shim -- lets you run `mdcat file.md` directly
test_mdcat.py   test suite (63 tests) -- python3 -m unittest test_mdcat -v
README.md       this file
```

## Known limitations

This is a **basic** renderer, not a full CommonMark implementation:

- Mid-word `*emphasis*` (e.g. `feas*ible*`) isn't supported — emphasis
  requires a non-word character (space/punctuation) on both sides. This
  is deliberate: it's what keeps things like `snake_case_names` and
  `2**10` from being misread as emphasis, which matters more for
  code-heavy Markdown than the rare mid-word case does.
- Blockquotes don't support "lazy" continuation lines (a wrapped line
  that omits the leading `>`); every line of a quote needs its own `>`.
- Frontmatter parsing is flat `key: value` only, matching the same
  restriction commonly used for this kind of format — no nested YAML.
- Bash/Python code highlighting is line-by-line (comments, strings,
  keywords); it doesn't track state across lines, so a string that
  spans multiple lines (e.g. a Python triple-quoted string) won't be
  highlighted correctly as one unit.
- No embedded HTML support (by design, per the original scope).
- Very wide tables shrink their columns proportionally rather than
  overflowing the terminal; extremely long single words (e.g. a long
  URL) may still overflow a line rather than being broken mid-word.

## Testing

```bash
python3 -m unittest test_mdcat -v
```

63 tests covering block parsing (including the fence-length-aware nested
code block case), inline rendering (including the double-backtick
escaped code span form and the snake_case/exponent emphasis guards),
syntax highlighting, tables, TOC/section extraction, the config file,
and CLI behavior end-to-end. All passing as of v0.1. Also verified by
hand against a real-world entry-authoring guide with several tricky
formatting cases (nested fences, bold wrapping a code span, etc.).

## Changelog

- **0.1** — initial release.
