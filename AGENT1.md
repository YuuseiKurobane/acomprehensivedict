# Agent 1 pipeline

The active pilot pipeline is:

```text
PDF
  -> pilot_agent1/intermediate/debug.json
  -> pilot_agent1/intermediate/human-readable.txt
  -> pilot_agent1/yomitan/*.json
  -> pilot_agent1/AComprehensive_agent1_pilot.zip
```

`human-readable.txt` is the lexical generation source. The Yomitan builder
parses that file from disk and has no API for raw PDF parser objects or debug
entries.

Run the default gold-page pilot:

```powershell
.venv\Scripts\python.exe extract_agent1.py
```

Build only from an edited human intermediate:

```powershell
.venv\Scripts\python.exe extract_agent1.py `
  --from-human pilot_agent1\intermediate\human-readable.txt `
  --output pilot_agent1_rebuilt
```

Run regression tests:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The first marker schema recognizes:

```text
[Entry]
[Variant]
[Homograph]
[Label]
[Pronunciation]
[Expansion]
[Definition]
[Definition N]
[Example]
[Translation]
[See]
[Subentry]
[Note]
[Unparsed]
```

Blank lines are cosmetic. Every nonblank line must begin with a recognized
marker. Optional-spelling expansion, accentless lookup aliases, source-label
colors, and clickable cross-references are Layer 2 operations performed while
building Yomitan.
