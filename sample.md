---
title: mdcat sample document
version: 0.2
status: reference
---

# mdcat sample document

This file exercises every construct the renderer supports. It doubles as the
fixture for the integration tests, so rendering it is a check that the whole
pipeline still works end to end.

## Text and emphasis

Paragraphs are reflowed to the terminal width, so the source can be wrapped at
whatever column is convenient without affecting the output. Inline styling
covers **bold**, *italic*, ***both at once***, `inline code`, ==highlight==,
~~strikethrough~~, [links](https://example.com/docs), bare autolinks such as
<https://example.com/>, and ![images](diagram.png), which appear as a
placeholder because a terminal cannot draw one.

Emphasis deliberately ignores markers inside words, so `snake_case_names` and
expressions like 2**10 survive intact.

## Lists

- A bulleted item, with a second level below it.
  - Nested items alternate their bullet glyph.
    - And a third level for good measure.
- An item long enough to wrap, which demonstrates the hanging indent that keeps
  continuation lines aligned under the text rather than under the bullet.

1. Ordered lists count automatically.
   1. Nested numbering restarts at one.
   2. And continues from there.
2. Back at the outer level.
   1. A second sublist starts again at one.

- [x] A completed task item
- [ ] An outstanding task item

A list item can carry a code sample:

- Check the queue before submitting:

  ```bash
  # only show jobs owned by the current user
  qstat -u "$USER" | grep -v " C "
  ```

- Then submit the job.

## Code

Fenced blocks are drawn as a shaded panel with line numbers. Bash and Python
fences get light keyword, string and comment highlighting:

```python
def summarise(records, limit=10):
    """Return the busiest entries, newest first."""
    ranked = sorted(records, key=lambda r: r.weight, reverse=True)
    return ranked[:limit]  # callers expect a plain list
```

Four-space indentation works too, and is left unhighlighted:

    export PATH="$HOME/bin:$PATH"
    hash -r

Fences nest correctly when the outer fence is longer than the inner one:

````markdown
```
an inner fence, shown literally
```
````

## Quotes

> A quoted paragraph is styled in italic and set off by a vertical bar. It is
> reflowed like any other paragraph, so the styling survives across wrapped
> lines.

> Quotes can hold structure too:
>
> - a list inside a quote
> - which keeps its shape
>
> ```
> and a code block
> ```

## Tables

| Option | Type | Default | Notes |
| :--- | :---: | ---: | :--- |
| `--width` | integer | detected | Minimum 20 |
| `--theme` | choice | `dark` | `dark` or `light` |
| `--ascii` | flag | off | Forced on when the encoding requires it |
| `a \| b` | escape | — | A literal pipe, escaped |
| `cmd \| filter` | code | — | A pipe inside a code span |

## Rules

Three or more dashes, asterisks or underscores draw a horizontal rule:

---

That is the end of the sample.
