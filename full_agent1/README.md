# Full Agent 1 pipeline

This folder separates the dictionary pipeline into two durable layers:

```text
acomprehensive.pdf
  -> Layer 1
  -> intermediate/human_readable_*.txt
  -> Layer 2
  -> yomitan/index.json + term_bank_*.json + ZIP
```

## Files

- `_main.py` — command-line workflow, profiles, paths, and JSON size options.
- `layer1_pdf_extractor.py` — PDF extraction adapter over the proven
  `../extract_agent1.py` parsing grammar. It pins the exact source SHA-256 and
  refuses to run if that frozen grammar changes accidentally.
- `layer2_yomitan_dictionary_writer.py` — standalone marker parser and Yomitan
  renderer. This is the file intended for future presentation changes.
- `intermediate/debug_*.json` — agent-facing PDF extraction evidence.
- `intermediate/human_readable_*.txt` — canonical human-readable generation
  sources.

Layer 1 preserves source labels and the literal `–`/`~` placeholders. Layer 2
resolves placeholders, creates lookup aliases, colors labels, and creates
clickable cross-references.

Every generated Yomitan JSON component is measured as UTF-8 and must be
strictly smaller than 8,000,000 bytes. Term banks are split automatically.

## Commands

Run these commands from `full_agent1`.

Generate the restricted regression-page intermediate:

```powershell
..\.venv\Scripts\python.exe _main.py layer1 --profile small
```

Output:

```text
intermediate/human_readable_small.txt
intermediate/debug_small.json
```

Generate the full dictionary intermediate from PDF pages 21–1123:

```powershell
..\.venv\Scripts\python.exe _main.py layer1 --profile full
```

Output:

```text
intermediate/human_readable_full.txt
intermediate/debug_full.json
```

Build the Yomitan dictionary from `human_readable_full.txt` only:

```powershell
..\.venv\Scripts\python.exe _main.py layer2 --profile full
```

Run both layers in one command:

```powershell
..\.venv\Scripts\python.exe _main.py all --profile full
```

Use custom pages while retaining the selected output profile name:

```powershell
..\.venv\Scripts\python.exe _main.py layer1 --profile small --pages 21-30,70,236,500
```

Use a custom marker file or stricter component limit:

```powershell
..\.venv\Scripts\python.exe _main.py layer2 `
  --input intermediate\human_readable_full.txt `
  --name AComprehensive_custom `
  --max-component-bytes 7000000
```
