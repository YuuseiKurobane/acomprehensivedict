# Full Agent 3 pipeline

This iteration keeps extraction conservative, preserves complete printed
headword lines, and moves lookup relationships and link resolution into
Layer 2:

```text
acomprehensive.pdf
  -> Layer 1: entries, senses, labels, cross-references, observed font runs
  -> intermediate/human_readable_*.txt
  -> Layer 2: self-naming content, badges, verified links, relationships
  -> yomitan/index.json + term_bank_*.json + report + ZIP
```

## Responsibility boundary

Layer 1 does not classify italic text as an example or following roman text as
a translation. Those roles are not reliably encoded by the PDF. Its body
markers are physical typography:

```text
[Roman]
[Italic]
[Bold]
[BoldItalic]
[SmallCaps]
[Symbol]
```

It retains the constrained structures `[Entry]`, `[Subentry]`, `[Variant]`,
`[Homograph]`, `[Sense]`, and `[Label]`. Later bold forms in the printed
headword header are indexed as variants, but their original connectors,
brackets, labels, order, and typography also remain in the run stream. Thus
`membubarkan [and ngebubarin (J coq)]` is not reduced to detached metadata.
An arrow immediately followed by small-cap text is retained as `[See]`;
unrelated small-cap text stays `[SmallCaps]`. The detailed debug JSON remains
the place to inspect page, line, font, geometry, raw text, and character
repairs.

Layer 1 repairs ASCII hyphens printed at physical PDF line wraps only when the
debug geometry reaches a known column edge and resumes at the same column's
start in the same source style. Corpus evidence determines whether to remove
a discretionary hyphen (`sau- dara` → `saudara`) or preserve a lexical hyphen
while removing the layout space (`asset- backed` → `asset-backed`). Remaining
cases are resolved by the reviewed `audit1_line_wrap_resolutions.csv`; its LLM
draft decisions are explicit and editable. Every build writes the applied and
unresolved evidence to `intermediate/audit1_line_wrap_<profile>.json`.

When the PDF isolates a boundary `~` or en dash in a Roman run directly beside
an italic phrase, Layer 1 moves the operator into that italic run and removes
the emptied Roman run. It does not move operators across labels, senses,
parentheses, or other structural markers.

Layer 2 owns all reader-facing cleanup:

- Consecutive homographs of one expression become one Yomitan result with
  Roman section markers such as `I`, `II`, and `III`.
- Every source form names itself at the start of its first displayed line.
  All lookup aliases share that same source-faithful content.
- Only a new Roman section or a numbered sense creates an explicit line.
  Definitions, Indonesian phrases/examples, English glosses/translations,
  labels, and cross-references otherwise stay inline and wrap naturally.
- Typography is preserved without claiming whether italic material is an
  example, collocation, cited form, or scientific name.
- A compound source label remains `[Label] J/Jv` in Layer 1. Layer 2 splits it
  only when every slash-separated component is a known label, producing the
  existing `Jakarta` and `Javanese` badge style.
- `[See] BANG III` retains the visible Roman/sense suffix. Layer 2 resolves
  targets against generated spellings, optional forms, repaired source
  spacing, unique expression prefixes, and source template phrases.
- A cross-reference becomes a hyperlink only when its generated query is
  guaranteed to resolve. Unresolved references remain visible as black arrow
  text and are listed in `cross_reference_report.json`.
- Explicit subentries form a one-level lexical graph. Root entries receive
  `Kata Turunan`; subentries receive `Kata Dasar` and sibling
  `Kata Terkait` links under star bullets. Inline bold aliases do not create
  hierarchy.
- Standalone `–` and `~` source operators are resolved only in Layer 2.
  Layer 1 may already have attached them to the correct italic run; Layer 2
  performs the lexical substitution. Layer 2 does not alter ASCII hyphens.
- Parentheses used only to wrap a source label are suppressed around the
  rendered badge. The underlying Layer 1 run sequence remains unchanged.

The Layer 2 parser can still read Agent 1
`[Definition]`/`[Example]`/`[Translation]` marker files for migration, but
Agent 3's Layer 1 never generates those speculative roles.

## Files

- `_main.py` — command-line workflow and paths.
- `layer1_pdf_extractor.py` — faithful marker writer over the pinned,
  low-level PDF/layout grammar in `../extract_agent1.py`.
- `layer2_yomitan_dictionary_writer.py` — compact Yomitan renderer.
- `acomprehensive.pdf` — local source PDF supplied for this iteration.

The low-level grammar SHA-256 is pinned. This prevents silent output changes if
`../extract_agent1.py` is edited later; review and update the pin deliberately
after rerunning regression checks.

Every generated JSON component is measured in UTF-8 and must remain strictly
below 8,000,000 bytes. Term banks split automatically. Layer 2 also validates
every generated structured-content node against the Yomitan v3 tags,
properties, and style subset it uses before writing the row. A final
dictionary-wide check rejects any internal hyperlink whose query is absent
from the generated term banks.

## Commands

Run from `full_agent3`.

Generate the regression-page intermediate:

```powershell
..\.venv\Scripts\python.exe _main.py layer1 --profile small
```

Generate the full intermediate from PDF pages 21–1123:

```powershell
..\.venv\Scripts\python.exe _main.py layer1 --profile full
```

Build only Layer 2 from an existing marker file:

```powershell
..\.venv\Scripts\python.exe _main.py layer2 --profile full
```

Run both layers:

```powershell
..\.venv\Scripts\python.exe _main.py all --profile full
```

Use custom pages, paths, a ZIP name, or a lower component limit:

```powershell
..\.venv\Scripts\python.exe _main.py `
  --intermediate-dir ..\work\agent3-intermediate `
  --yomitan-dir ..\work\agent3-yomitan `
  all `
  --profile small `
  --pages 21-30,70,236,500 `
  --name AComprehensive_custom `
  --max-component-bytes 7000000
```

Run focused regressions:

```powershell
..\.venv\Scripts\python.exe -m unittest -v test_pipeline.py
```

## Honest limitations

The PDF still requires layout interpretation to find headword and sense
boundaries, and arrow-plus-small-caps is treated as a high-confidence
cross-reference convention. Layer 1 does not infer finer lexical semantics
from italics, line wrapping, or punctuation. Ambiguous material therefore
remains styled source text rather than being upgraded into a possibly false
dictionary category.
