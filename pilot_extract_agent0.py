#!/usr/bin/env python3
"""
Semantic pilot extractor for A Comprehensive Indonesian-English Dictionary.

The durable intermediate output is pretty-printed, entry-centric JSON, supported by
a compact CSV form index and a persistent manual-override CSV. Low-level PDF geometry
is used internally but is not emitted as the working format.

It also builds an explicitly non-final Yomitan ZIP for visual inspection. Ambiguous
italics remain italic and are never discarded or silently assigned a semantic role.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import random
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import pymupdf


SCHEMA_VERSION = "agent0-semantic-pilot-2"
SCRIPT_VERSION = "0.2.0"
DEFAULT_PAGES = "21-30,70,500"

# Verified against the rendered PDF, not guessed from extracted text.
# Raw characters are always retained alongside normalized text.
CHAR_NORMALIZATION = {
    "\u00a4": ("fi", "legacy Indrev font ligature mapping; rendered glyph is fi"),
    "\u00b6": ("fl", "legacy Indrev font ligature mapping; rendered glyph is fl"),
    "\u00d8": ("1/5", "legacy fraction glyph mapping; rendered glyph is 1/5"),
    "\u2030": ("1/6", "legacy fraction glyph mapping; rendered glyph is 1/6"),
    "\u00b3": ("1/3", "legacy fraction glyph mapping; rendered glyph is 1/3"),
    "\u00bf": ("1/12", "legacy fraction glyph mapping; rendered glyph is 1/12"),
    "\u00ad": ("", "discretionary soft hyphen; not part of the lexical text"),
}

BOLD_FONTS = {"Indrev-Bold", "Indrev-BoldItalic"}
ITALIC_FONTS = {"Indrev-Italic", "Indrev-BoldItalic", "Indrev-Italic-SC700"}
SMALL_CAPS_FONTS = {"Inresc-Roman", "Indrev-Roman-SC700", "Indrev-Bold-SC700"}
ROOT_X = (42.0, 258.0)
DERIVATIVE_X = (48.0, 264.0)
BODY_Y_MIN = 50.0
BODY_Y_MAX = 690.0
ROMAN_NUMERAL_RE = re.compile(r"^(.*?)(?:\s+)([IVXLCDM]+)\s*$")
BINOMIAL_RE = re.compile(r"^[A-Z][a-zéë-]+(?:\s+[a-zéë-]+){1,2}[,.]?$")


def json_dump(obj: Any, *, indent: int | None = None) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=indent, separators=None if indent else (",", ":"))


def parse_page_spec(spec: str, page_count: int) -> list[int]:
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            pages.update(range(int(a), int(b) + 1))
        else:
            pages.add(int(part))
    result = sorted(p for p in pages if 1 <= p <= page_count)
    if not result:
        raise ValueError("Page selection is empty.")
    return result


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_text(text: str) -> tuple[str, list[dict[str, Any]]]:
    output: list[str] = []
    events: list[dict[str, Any]] = []
    for offset, char in enumerate(text):
        replacement = CHAR_NORMALIZATION.get(char)
        if replacement is None:
            output.append(char)
            continue
        value, reason = replacement
        output.append(value)
        events.append(
            {
                "raw_offset": offset,
                "raw": char,
                "raw_codepoint": f"U+{ord(char):04X}",
                "replacement": value,
                "reason": reason,
            }
        )
    return "".join(output), events


def normalize_lookup(text: str) -> str:
    """Fold dictionary pronunciation diacritics for real-world Indonesian lookup."""
    transliteration = str.maketrans(
        {
            "Ø": "O",
            "ø": "o",
            "Ł": "L",
            "ł": "l",
            "Đ": "D",
            "đ": "d",
            "Ð": "D",
            "ð": "d",
            "Þ": "Th",
            "þ": "th",
            "Æ": "AE",
            "æ": "ae",
            "Œ": "OE",
            "œ": "oe",
        }
    )
    decomposed = unicodedata.normalize("NFKD", text.translate(transliteration))
    folded = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", folded).strip()


def load_tag_map(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return {row["tag"]: row for row in csv.DictReader(f)}


def rect_list(value: Any) -> list[float]:
    return [round(float(x), 4) for x in value]


def color_hex(value: int) -> str:
    return f"#{value & 0xFFFFFF:06X}"


def serialize_page(page: pymupdf.Page, page_number: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw = page.get_text("rawdict")
    page_obj: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "pdf_page_number": page_number,
        "pdf_page_index": page_number - 1,
        "width": round(page.rect.width, 4),
        "height": round(page.rect.height, 4),
        "rotation": page.rotation,
        "text_blocks": [],
        "non_text_blocks": [],
    }
    reading_lines: list[dict[str, Any]] = []
    span_serial = 0
    line_serial = 0

    for block_index, block in enumerate(raw.get("blocks", [])):
        if block.get("type") != 0:
            page_obj["non_text_blocks"].append(
                {
                    "block_index": block_index,
                    "type": block.get("type"),
                    "bbox": rect_list(block.get("bbox", (0, 0, 0, 0))),
                    "width": block.get("width"),
                    "height": block.get("height"),
                    "extension": block.get("ext"),
                    "image_byte_length": len(block.get("image", b"")) if isinstance(block.get("image"), bytes) else None,
                }
            )
            continue

        block_obj = {
            "block_id": f"p{page_number:04d}-b{block_index:04d}",
            "block_index": block_index,
            "bbox": rect_list(block.get("bbox", (0, 0, 0, 0))),
            "lines": [],
        }
        for source_line_index, line in enumerate(block.get("lines", [])):
            line_serial += 1
            line_id = f"p{page_number:04d}-l{line_serial:04d}"
            line_obj: dict[str, Any] = {
                "line_id": line_id,
                "source_line_index": source_line_index,
                "bbox": rect_list(line.get("bbox", (0, 0, 0, 0))),
                "writing_mode": line.get("wmode"),
                "direction": rect_list(line.get("dir", (1, 0))),
                "spans": [],
            }
            line_raw_parts: list[str] = []
            line_norm_parts: list[str] = []

            for source_span_index, span in enumerate(line.get("spans", [])):
                span_serial += 1
                span_id = f"p{page_number:04d}-s{span_serial:05d}"
                chars = []
                raw_text_parts: list[str] = []
                for char_index, char in enumerate(span.get("chars", [])):
                    c = char.get("c", "")
                    raw_text_parts.append(c)
                    chars.append(
                        {
                            "char_index": char_index,
                            "raw": c,
                            "codepoint": f"U+{ord(c):04X}" if c else None,
                            "origin": rect_list(char.get("origin", (0, 0))),
                            "bbox": rect_list(char.get("bbox", (0, 0, 0, 0))),
                            "synthetic": char.get("synthetic", False),
                        }
                    )
                raw_text = "".join(raw_text_parts)
                normalized_text, events = normalize_text(raw_text)
                span_obj = {
                    "span_id": span_id,
                    "source_span_index": source_span_index,
                    "font": span.get("font"),
                    "size": round(float(span.get("size", 0)), 4),
                    "flags": span.get("flags"),
                    "bidi": span.get("bidi"),
                    "char_flags": span.get("char_flags"),
                    "color": color_hex(int(span.get("color", 0))),
                    "alpha": span.get("alpha"),
                    "ascender": round(float(span.get("ascender", 0)), 6),
                    "descender": round(float(span.get("descender", 0)), 6),
                    "origin": rect_list(span.get("origin", (0, 0))),
                    "bbox": rect_list(span.get("bbox", (0, 0, 0, 0))),
                    "raw_text": raw_text,
                    "normalized_text": normalized_text,
                    "normalization_events": events,
                    "chars": chars,
                }
                line_obj["spans"].append(span_obj)
                line_raw_parts.append(raw_text)
                line_norm_parts.append(normalized_text)

            line_obj["raw_text"] = "".join(line_raw_parts)
            line_obj["normalized_text"] = "".join(line_norm_parts)
            line_obj["column"] = 0 if line_obj["bbox"][0] < page.rect.width / 2 else 1
            block_obj["lines"].append(line_obj)
            reading_lines.append(line_obj)
        page_obj["text_blocks"].append(block_obj)

    reading_lines.sort(key=lambda x: (x["column"], round(x["bbox"][1], 1), x["bbox"][0]))
    for order, line in enumerate(reading_lines):
        line["page_reading_order"] = order
    return page_obj, reading_lines


def meaningful_spans(line: dict[str, Any]) -> list[dict[str, Any]]:
    return [span for span in line["spans"] if span["raw_text"].strip()]


def near(value: float, targets: Iterable[float], tolerance: float = 1.25) -> bool:
    return any(abs(value - target) <= tolerance for target in targets)


def classify_line(line: dict[str, Any]) -> str:
    y = line["bbox"][1]
    if y < BODY_Y_MIN:
        return "running_header"
    if y > BODY_Y_MAX:
        return "running_footer"
    spans = meaningful_spans(line)
    if not spans:
        return "blank"
    first = spans[0]
    x = first["bbox"][0]
    font = first["font"]
    if font in BOLD_FONTS and near(x, ROOT_X):
        return "root_entry_start"
    if font in BOLD_FONTS and near(x, DERIVATIVE_X):
        return "derivative_start"
    return "continuation"


def split_homograph(text: str) -> tuple[str, str | None]:
    clean = text.strip()
    match = ROMAN_NUMERAL_RE.match(clean)
    if match:
        return match.group(1).strip(), match.group(2)
    return clean, None


def tag_codes_from_italic(text: str, tag_map: dict[str, dict[str, str]]) -> list[str]:
    clean = text.strip().strip("()[]").strip()
    if clean in tag_map:
        return [clean]
    tokens = clean.split()
    if tokens and all(token in tag_map for token in tokens):
        return tokens
    return []


def classify_italic(
    span: dict[str, Any],
    line: dict[str, Any],
    span_index: int,
    tag_map: dict[str, dict[str, str]],
) -> dict[str, Any]:
    text = span["normalized_text"].strip()
    codes = tag_codes_from_italic(text, tag_map)
    if codes:
        return {"role": "label", "confidence": 0.99, "tag_codes": codes}
    if BINOMIAL_RE.match(text):
        return {"role": "scientific_name_candidate", "confidence": 0.9}

    before = "".join(s["normalized_text"] for s in line["spans"][:span_index]).rstrip()
    after = "".join(s["normalized_text"] for s in line["spans"][span_index + 1 :]).lstrip()
    word_count = len(re.findall(r"[A-Za-zÀ-ÿ]+", text))
    if "–" in before[-4:] or "~" in text or (word_count >= 2 and after):
        return {"role": "example_or_phrase_candidate", "confidence": 0.65}
    return {"role": "italic_unclassified", "confidence": 0.0}


def line_annotations(line: dict[str, Any], tag_map: dict[str, dict[str, str]]) -> dict[str, Any]:
    annotations: dict[str, Any] = {
        "line_id": line["line_id"],
        "line_class": classify_line(line),
        "span_roles": [],
        "sense_candidates": [],
        "labels": [],
        "cross_references": [],
        "italic_runs": [],
    }
    spans = line["spans"]
    for index, span in enumerate(spans):
        text = span["normalized_text"]
        role = None
        if span["font"] in SMALL_CAPS_FONTS and text.strip():
            role = "cross_reference_target"
            annotations["cross_references"].append(
                {
                    "display": text.strip(),
                    "query_candidate": normalize_lookup(text.strip()).lower(),
                    "span_id": span["span_id"],
                }
            )
        elif span["font"] == "SymbolMT" and "→" in text:
            role = "cross_reference_arrow"
        elif span["font"] in BOLD_FONTS and re.fullmatch(r"\s*\d+\s*", text):
            role = "sense_number_candidate"
            annotations["sense_candidates"].append(
                {"number": int(text.strip()), "span_id": span["span_id"]}
            )
        elif span["font"] in ITALIC_FONTS and text.strip():
            italic = classify_italic(span, line, index, tag_map)
            role = italic["role"]
            italic["span_id"] = span["span_id"]
            italic["text"] = text
            annotations["italic_runs"].append(italic)
            if role == "label":
                annotations["labels"].append(italic)
        elif span["font"] in BOLD_FONTS:
            role = "bold_lexical_or_structural"
        else:
            role = "roman_text"
        annotations["span_roles"].append({"span_id": span["span_id"], "role": role})
    return annotations


def first_bold_text(line: dict[str, Any]) -> str:
    for span in meaningful_spans(line):
        if span["font"] in BOLD_FONTS:
            return span["normalized_text"].strip()
    return ""


def build_entries(
    selected_pages: list[int],
    page_lines: dict[int, list[dict[str, Any]]],
    tag_map: dict[str, dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_form: dict[str, Any] | None = None
    previous_page: int | None = None
    metrics = Counter()

    def close_current(partial_end: bool = False) -> None:
        nonlocal current, current_form
        if current is not None:
            current["partial_end"] = partial_end
            current["source_pages"] = sorted(set(current["source_pages"]))
            entries.append(current)
        current = None
        current_form = None

    for page_number in selected_pages:
        if previous_page is not None and page_number != previous_page + 1:
            close_current(partial_end=True)
        previous_page = page_number

        for line in page_lines[page_number]:
            annotations = line_annotations(line, tag_map)
            line_class = annotations["line_class"]
            metrics[f"line:{line_class}"] += 1
            for italic in annotations["italic_runs"]:
                metrics[f"italic:{italic['role']}"] += 1
            metrics["labels"] += len(annotations["labels"])
            metrics["cross_references"] += len(annotations["cross_references"])
            metrics["sense_candidates"] += len(annotations["sense_candidates"])

            if line_class in {"running_header", "running_footer", "blank"}:
                continue

            if line_class == "root_entry_start":
                close_current()
                raw_headword = first_bold_text(line)
                headword, homograph = split_homograph(raw_headword)
                current = {
                    "schema_version": SCHEMA_VERSION,
                    "entry_id": f"entry-{len(entries) + 1:06d}",
                    "entry_kind": "root",
                    "headword_candidate": headword,
                    "headword_raw_candidate": raw_headword,
                    "homograph_candidate": homograph,
                    "source_pages": [page_number],
                    "line_refs": [],
                    "forms": [],
                    "labels": [],
                    "sense_candidates": [],
                    "cross_references": [],
                    "italic_runs": [],
                    "partial_start": False,
                    "partial_end": False,
                    "parser_warnings": [],
                }
                current_form = {
                    "form_kind": "root",
                    "expression_candidate": headword,
                    "homograph_candidate": homograph,
                    "line_refs": [],
                }
                current["forms"].append(current_form)
                metrics["root_entries"] += 1

            elif line_class == "derivative_start" and current is not None:
                raw_form = first_bold_text(line)
                expression, homograph = split_homograph(raw_form)
                current_form = {
                    "form_kind": "derivative",
                    "expression_candidate": expression,
                    "homograph_candidate": homograph,
                    "line_refs": [],
                }
                current["forms"].append(current_form)
                metrics["derivative_forms"] += 1

            elif current is None:
                current = {
                    "schema_version": SCHEMA_VERSION,
                    "entry_id": f"fragment-{len(entries) + 1:06d}",
                    "entry_kind": "orphan_fragment",
                    "headword_candidate": None,
                    "headword_raw_candidate": None,
                    "homograph_candidate": None,
                    "source_pages": [page_number],
                    "line_refs": [],
                    "forms": [],
                    "labels": [],
                    "sense_candidates": [],
                    "cross_references": [],
                    "italic_runs": [],
                    "partial_start": True,
                    "partial_end": False,
                    "parser_warnings": ["Selected page begins in the middle of an entry."],
                }
                current_form = None
                metrics["orphan_fragments"] += 1

            assert current is not None
            current["source_pages"].append(page_number)
            current["line_refs"].append(line["line_id"])
            current["labels"].extend(annotations["labels"])
            current["sense_candidates"].extend(annotations["sense_candidates"])
            current["cross_references"].extend(annotations["cross_references"])
            current["italic_runs"].extend(annotations["italic_runs"])
            if current_form is not None:
                current_form["line_refs"].append(line["line_id"])

    close_current(partial_end=True)
    return entries, dict(sorted(metrics.items()))


def full_character_audit(doc: pymupdf.Document) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    fonts: dict[str, Counter[str]] = defaultdict(Counter)
    contexts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page_number, page in enumerate(doc, 1):
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    counts.update(text)
                    for index, char in enumerate(text):
                        if ord(char) > 126 or unicodedata.category(char).startswith("C"):
                            fonts[char][span.get("font", "")] += 1
                            if len(contexts[char]) < 8:
                                contexts[char].append(
                                    {
                                        "page": page_number,
                                        "font": span.get("font"),
                                        "context": text[max(0, index - 30) : index + 31],
                                    }
                                )
    rows = []
    for char, count in sorted(counts.items(), key=lambda item: (-item[1], ord(item[0]))):
        if ord(char) <= 126 and not unicodedata.category(char).startswith("C"):
            continue
        normalized = CHAR_NORMALIZATION.get(char)
        rows.append(
            {
                "codepoint": f"U+{ord(char):04X}",
                "raw": char,
                "unicode_name": unicodedata.name(char, "<unnamed>"),
                "unicode_category": unicodedata.category(char),
                "count": count,
                "fonts": fonts[char].most_common(),
                "normalization": normalized[0] if normalized else None,
                "normalization_reason": normalized[1] if normalized else None,
                "contexts": contexts[char],
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "page_count_scanned": doc.page_count,
        "characters": rows,
    }


def expanded_labels_for_lines(
    line_refs: list[str],
    line_lookup: dict[str, dict[str, Any]],
    tag_map: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    labels: list[dict[str, str]] = []
    seen: set[str] = set()
    for line_id in line_refs:
        line = line_lookup[line_id]
        for span in line["spans"]:
            if span["font"] not in ITALIC_FONTS:
                continue
            for code in tag_codes_from_italic(span["normalized_text"], tag_map):
                if code in seen:
                    continue
                seen.add(code)
                row = tag_map[code]
                labels.append(
                    {
                        "code": code,
                        "name": row["tag_rename"],
                        "type": row["type"],
                        "color": row["color"],
                    }
                )
    return labels


def semantic_span_nodes(
    span: dict[str, Any],
    tag_map: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    text = span["normalized_text"]
    if not text:
        return []
    stripped = text.strip()
    codes = tag_codes_from_italic(stripped, tag_map) if span["font"] in ITALIC_FONTS else []
    if codes:
        return [
            {
                "type": "label",
                "code": code,
                "name": tag_map[code]["tag_rename"],
                "color": tag_map[code]["color"],
            }
            for code in codes
        ]
    if span["font"] in SMALL_CAPS_FONTS and stripped:
        return [
            {
                "type": "cross_reference_target",
                "display": stripped,
                "search": normalize_lookup(stripped).lower(),
                "style": "small_caps",
            }
        ]

    if span["font"] == "Indrev-BoldItalic":
        style = "bold_italic"
    elif span["font"] in BOLD_FONTS:
        style = "bold"
    elif span["font"] in ITALIC_FONTS:
        style = "italic"
    else:
        style = "roman"
    node: dict[str, Any] = {"type": "text", "text_pdf": text, "style": style}
    if span["raw_text"] != text:
        node["text_extracted"] = span["raw_text"]
        node["extraction_repairs"] = span["normalization_events"]
    return [node]


def semantic_line(
    line: dict[str, Any],
    tag_map: dict[str, dict[str, str]],
) -> dict[str, Any]:
    content = []
    for span in line["spans"]:
        content.extend(semantic_span_nodes(span, tag_map))
    return {
        "line_id": line["line_id"],
        "line_class": classify_line(line),
        "text_pdf": line["normalized_text"],
        "content": content,
    }


def source_markup_for_lines(
    line_refs: list[str],
    line_lookup: dict[str, dict[str, Any]],
    tag_map: dict[str, dict[str, str]],
) -> str:
    lines = []
    for line_id in line_refs:
        parts = []
        for span in line_lookup[line_id]["spans"]:
            text = html.escape(span["normalized_text"])
            if not text:
                continue
            codes = (
                tag_codes_from_italic(span["normalized_text"], tag_map)
                if span["font"] in ITALIC_FONTS
                else []
            )
            if codes:
                for index, code in enumerate(codes):
                    if index:
                        parts.append(" ")
                    parts.append(
                        f'<label code="{html.escape(code, quote=True)}">'
                        f'{html.escape(tag_map[code]["tag_rename"])}</label>'
                    )
            elif span["font"] in SMALL_CAPS_FONTS:
                parts.append(f"<xref>{text}</xref>")
            elif span["font"] == "Indrev-BoldItalic":
                parts.append(f"<b><i>{text}</i></b>")
            elif span["font"] in BOLD_FONTS:
                parts.append(f"<b>{text}</b>")
            elif span["font"] in ITALIC_FONTS:
                parts.append(f"<i>{text}</i>")
            else:
                parts.append(text)
        lines.append("".join(parts))
    return "\n".join(lines)


def build_semantic_entries(
    entries: list[dict[str, Any]],
    line_lookup: dict[str, dict[str, Any]],
    tag_map: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    semantic_entries = []
    for entry in entries:
        headword_pdf = entry.get("headword_candidate")
        headword_search = normalize_lookup(headword_pdf) if headword_pdf else None
        semantic_forms = []
        root_form_id = None
        for form_index, form in enumerate(entry.get("forms", []), 1):
            expression_pdf = form.get("expression_candidate") or ""
            expression_search = normalize_lookup(expression_pdf)
            form_id = f"{entry['entry_id']}-f{form_index:03d}"
            if form["form_kind"] == "root":
                root_form_id = form_id
            semantic_forms.append(
                {
                    "form_id": form_id,
                    "parent_form_id": None if form["form_kind"] == "root" else root_form_id,
                    "form_order": form_index,
                    "form_type": form["form_kind"],
                    "expression": {
                        "pdf": expression_pdf,
                        "search": expression_search,
                        "reading": expression_pdf if expression_pdf != expression_search else "",
                    },
                    "homograph": form.get("homograph_candidate"),
                    "labels": expanded_labels_for_lines(form["line_refs"], line_lookup, tag_map),
                    "content": [
                        semantic_line(line_lookup[line_id], tag_map)
                        for line_id in form["line_refs"]
                    ],
                    "source_markup": source_markup_for_lines(
                        form["line_refs"], line_lookup, tag_map
                    ),
                }
            )

        cross_references = []
        for item in entry.get("cross_references", []):
            cross_references.append(
                {
                    "display": item["display"],
                    "search": normalize_lookup(item["display"]).lower(),
                    "resolved_form_id": None,
                    "status": "unresolved",
                }
            )
        italic_annotations = [
            {
                "role": item["role"],
                "text": item["text"],
                "confidence": item["confidence"],
            }
            for item in entry.get("italic_runs", [])
            if item["role"] != "label"
        ]
        semantic_entries.append(
            {
                "schema_version": SCHEMA_VERSION,
                "entry_id": entry["entry_id"],
                "source_order": len(semantic_entries) + 1,
                "entry_type": entry["entry_kind"],
                "source": {
                    "pdf_pages": entry["source_pages"],
                    "partial_start": entry["partial_start"],
                    "partial_end": entry["partial_end"],
                },
                "headword": {
                    "pdf": headword_pdf,
                    "search": headword_search,
                    "reading": (
                        headword_pdf
                        if headword_pdf and headword_pdf != headword_search
                        else ""
                    ),
                    "homograph": entry.get("homograph_candidate"),
                },
                "labels": expanded_labels_for_lines(
                    entry["line_refs"], line_lookup, tag_map
                ),
                "forms": semantic_forms,
                "sense_markers": [
                    {"number": item["number"]} for item in entry.get("sense_candidates", [])
                ],
                "cross_references": cross_references,
                "italic_annotations": italic_annotations,
                "unassigned_content": (
                    [
                        semantic_line(line_lookup[line_id], tag_map)
                        for line_id in entry["line_refs"]
                    ]
                    if not semantic_forms
                    else []
                ),
                "source_markup": source_markup_for_lines(
                    entry["line_refs"], line_lookup, tag_map
                ),
                "parser_warnings": entry.get("parser_warnings", []),
                "review_status": "unreviewed",
                "manual_note": "",
            }
        )
    return semantic_entries


def write_semantic_intermediate(
    output_dir: Path,
    semantic_entries: list[dict[str, Any]],
    max_entries_per_chunk: int = 250,
) -> dict[str, Any]:
    intermediate_dir = output_dir / "intermediate"
    entries_dir = intermediate_dir / "entries"
    entries_dir.mkdir(parents=True, exist_ok=True)
    for stale in entries_dir.glob("*.json"):
        stale.unlink()

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in semantic_entries:
        search = entry["headword"].get("search") or ""
        initial = search[:1].lower()
        group = initial if "a" <= initial <= "z" else "_"
        groups[group].append(entry)

    chunk_by_entry: dict[str, str] = {}
    chunk_files = []
    for group in sorted(groups):
        group_entries = groups[group]
        for start in range(0, len(group_entries), max_entries_per_chunk):
            chunk_number = start // max_entries_per_chunk + 1
            filename = f"{group}_{chunk_number:03d}.json"
            relative = f"entries/{filename}"
            chunk = group_entries[start : start + max_entries_per_chunk]
            payload = {
                "schema_version": SCHEMA_VERSION,
                "group": group,
                "chunk_number": chunk_number,
                "entries": chunk,
            }
            (entries_dir / filename).write_text(
                json_dump(payload, indent=2) + "\n", encoding="utf-8"
            )
            chunk_files.append(f"intermediate/{relative}")
            for entry in chunk:
                chunk_by_entry[entry["entry_id"]] = relative

    index_path = intermediate_dir / "index.csv"
    fields = [
        "form_id",
        "entry_id",
        "parent_form_id",
        "form_type",
        "expression_pdf",
        "expression_search",
        "reading",
        "homograph",
        "labels",
        "pdf_pages",
        "chunk_file",
        "review_status",
    ]
    with index_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for entry in semantic_entries:
            for form in entry["forms"]:
                writer.writerow(
                    {
                        "form_id": form["form_id"],
                        "entry_id": entry["entry_id"],
                        "parent_form_id": form["parent_form_id"] or "",
                        "form_type": form["form_type"],
                        "expression_pdf": form["expression"]["pdf"],
                        "expression_search": form["expression"]["search"],
                        "reading": form["expression"]["reading"],
                        "homograph": form["homograph"] or "",
                        "labels": " | ".join(label["code"] for label in form["labels"]),
                        "pdf_pages": " | ".join(str(p) for p in entry["source"]["pdf_pages"]),
                        "chunk_file": chunk_by_entry[entry["entry_id"]],
                        "review_status": entry["review_status"],
                    }
                )

    overrides_path = intermediate_dir / "overrides.csv"
    if not overrides_path.exists():
        with overrides_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "override_id",
                    "target_type",
                    "target_id",
                    "field",
                    "old_value",
                    "new_value",
                    "reason",
                    "reviewer",
                ]
            )
    return {
        "directory": "intermediate",
        "entry_chunks": chunk_files,
        "index": "intermediate/index.csv",
        "overrides": "intermediate/overrides.csv",
    }


def span_to_structured(span: dict[str, Any], tag_map: dict[str, dict[str, str]]) -> Any:
    text = span["normalized_text"]
    if not text:
        return ""
    stripped = text.strip()
    codes = tag_codes_from_italic(stripped, tag_map) if span["font"] in ITALIC_FONTS else []
    if codes:
        badges = []
        for index, code in enumerate(codes):
            row = tag_map[code]
            if index:
                badges.append(" ")
            badges.append(
                {
                    "tag": "span",
                    "title": f"Source abbreviation: {code}",
                    "style": {
                        "fontSize": "0.8em",
                        "fontWeight": "bold",
                        "padding": "0.1em 0.25em",
                        "borderRadius": "0.3em",
                        "backgroundColor": row["color"],
                        "color": "white",
                        "wordBreak": "keep-all",
                    },
                    "content": row["tag_rename"],
                }
            )
        return badges
    if span["font"] in SMALL_CAPS_FONTS and stripped:
        return {
            "tag": "a",
            "href": f"?query={quote(normalize_lookup(stripped).lower())}",
            "content": text,
        }
    style: dict[str, str] = {}
    if span["font"] in BOLD_FONTS:
        style["fontWeight"] = "bold"
    if span["font"] in ITALIC_FONTS:
        style["fontStyle"] = "italic"
    if style:
        return {"tag": "span", "style": style, "content": text}
    return text


def build_yomitan(
    output_dir: Path,
    entries: list[dict[str, Any]],
    line_lookup: dict[str, dict[str, Any]],
    tag_map: dict[str, dict[str, str]],
) -> tuple[Path, int]:
    yomitan_dir = output_dir / "yomitan"
    yomitan_dir.mkdir(parents=True, exist_ok=True)
    index = {
        "title": "A Comprehensive Indonesian-English Dictionary (agent0 semantic pilot)",
        "revision": "agent0-pilot-0.2.0",
        "format": 3,
        "sequenced": True,
        "author": "Pilot extraction; source by Alan M. Stevens and A. Ed. Schmidgall-Tellings",
        "description": "Non-final layout-preserving extraction pilot. Semantic classification is intentionally conservative.",
        "attribution": "For private extraction testing. Confirm redistribution rights before publication.",
        "sourceLanguage": "id",
        "targetLanguage": "en",
    }
    (yomitan_dir / "index.json").write_text(json_dump(index, indent=2) + "\n", encoding="utf-8")

    term_rows = []
    sequence = 0

    def append_term_rows(
        expression_pdf: str,
        glossary: list[dict[str, Any]],
        sequence_number: int,
    ) -> None:
        expression_search = normalize_lookup(expression_pdf)
        reading = expression_pdf if expression_pdf != expression_search else ""
        term_rows.append(
            [
                expression_search,
                reading,
                "",
                "",
                0,
                glossary,
                sequence_number,
                "",
            ]
        )
        if expression_pdf != expression_search:
            term_rows.append(
                [
                    expression_pdf,
                    "",
                    "",
                    "",
                    0,
                    glossary,
                    sequence_number,
                    "",
                ]
            )

    for entry in entries:
        if entry["entry_kind"] != "root" or not entry["headword_candidate"]:
            continue
        sequence += 1
        content_lines = []
        for line_id in entry["line_refs"]:
            line = line_lookup[line_id]
            nodes = [span_to_structured(span, tag_map) for span in line["spans"]]
            nodes = [node for node in nodes if node != ""]
            content_lines.append({"tag": "div", "content": nodes})
        source_text = ", ".join(f"PDF p. {p}" for p in entry["source_pages"])
        content_lines.append(
            {
                "tag": "div",
                "style": {"fontSize": "0.7em", "textAlign": "right"},
                "content": source_text,
            }
        )
        glossary = [{"type": "structured-content", "content": content_lines}]
        append_term_rows(entry["headword_candidate"], glossary, sequence)

        for form in entry["forms"]:
            if form["form_kind"] != "derivative" or not form["expression_candidate"]:
                continue
            form_lines = []
            for line_id in form["line_refs"]:
                line = line_lookup[line_id]
                nodes = [span_to_structured(span, tag_map) for span in line["spans"]]
                nodes = [node for node in nodes if node != ""]
                form_lines.append({"tag": "div", "content": nodes})
            form_lines.append(
                {
                    "tag": "div",
                    "style": {"fontSize": "0.8em"},
                    "content": [
                        "Root entry: ",
                        {
                            "tag": "a",
                            "href": f"?query={quote(normalize_lookup(entry['headword_candidate']))}",
                            "content": entry["headword_candidate"],
                        },
                    ],
                }
            )
            append_term_rows(
                form["expression_candidate"],
                [{"type": "structured-content", "content": form_lines}],
                sequence,
            )

    bank_path = yomitan_dir / "term_bank_1.json"
    bank_path.write_text(json_dump(term_rows) + "\n", encoding="utf-8")
    zip_path = output_dir / "AComprehensive_agent0_pilot.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.write(yomitan_dir / "index.json", "index.json")
        zf.write(bank_path, "term_bank_1.json")
    return zip_path, len(term_rows)


def render_representative_pages(
    doc: pymupdf.Document,
    output_dir: Path,
    selected_pages: list[int],
) -> list[str]:
    candidates = [p for p in (21, 70, 500) if p in selected_pages]
    render_dir = output_dir / "rendered"
    render_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for page_number in candidates:
        path = render_dir / f"page_{page_number:04d}.png"
        doc[page_number - 1].get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).save(path)
        paths.append(str(path.name))
    return paths


def review_span_html(span: dict[str, Any], tag_map: dict[str, dict[str, str]]) -> str:
    text = span["normalized_text"]
    if not text:
        return ""
    escaped = html.escape(text)
    stripped = text.strip()
    codes = tag_codes_from_italic(stripped, tag_map) if span["font"] in ITALIC_FONTS else []
    if len(codes) == 1:
        row = tag_map[codes[0]]
        return (
            f'<span class="tag" title="{html.escape(row["tag_rename"], quote=True)}" '
            f'style="--tag-color:{html.escape(row["color"], quote=True)}">{html.escape(stripped)}</span>'
        )
    if span["font"] in SMALL_CAPS_FONTS and stripped:
        return f'<span class="xref" title="Cross-reference candidate">{escaped}</span>'
    classes = []
    if span["font"] in BOLD_FONTS:
        classes.append("bold")
    if span["font"] in ITALIC_FONTS:
        classes.append("italic")
    if classes:
        return f'<span class="{" ".join(classes)}">{escaped}</span>'
    return escaped


def build_review_html(
    output_dir: Path,
    entries: list[dict[str, Any]],
    line_lookup: dict[str, dict[str, Any]],
    tag_map: dict[str, dict[str, str]],
    selected_pages: list[int],
) -> Path:
    cards: list[str] = []
    for entry in entries:
        headword = entry.get("headword_candidate") or "(orphan fragment)"
        homograph = entry.get("homograph_candidate")
        title = f"{headword} {homograph or ''}".strip()
        pages = entry.get("source_pages", [])
        page_text = ", ".join(str(p) for p in pages)
        unclassified = sum(1 for item in entry["italic_runs"] if item["role"] == "italic_unclassified")
        example_candidates = sum(
            1 for item in entry["italic_runs"] if item["role"] == "example_or_phrase_candidate"
        )
        scientific = sum(
            1 for item in entry["italic_runs"] if item["role"] == "scientific_name_candidate"
        )
        labels = []
        for item in entry["labels"]:
            labels.extend(item.get("tag_codes", []))
        labels = list(dict.fromkeys(labels))
        issue_tokens = []
        if unclassified:
            issue_tokens.append("unclassified")
        if entry.get("partial_start") or entry.get("partial_end"):
            issue_tokens.append("partial")
        if not entry.get("headword_candidate"):
            issue_tokens.append("no-headword")
        if len(entry.get("forms", [])) > 1:
            issue_tokens.append("multiple-forms")

        line_html = []
        for line_id in entry["line_refs"]:
            line = line_lookup[line_id]
            rendered = "".join(review_span_html(span, tag_map) for span in line["spans"])
            line_html.append(
                f'<div class="source-line" data-line-id="{html.escape(line_id, quote=True)}">{rendered}</div>'
            )

        badges = []
        for code in labels:
            row = tag_map.get(code)
            if row:
                badges.append(
                    f'<span class="tag" style="--tag-color:{html.escape(row["color"], quote=True)}" '
                    f'title="{html.escape(row["tag_rename"], quote=True)}">{html.escape(code)}</span>'
                )
        if unclassified:
            badges.append(f'<span class="metric warn">{unclassified} unresolved italic</span>')
        if example_candidates:
            badges.append(f'<span class="metric">{example_candidates} example/phrase?</span>')
        if scientific:
            badges.append(f'<span class="metric">{scientific} scientific?</span>')
        if entry.get("sense_candidates"):
            badges.append(f'<span class="metric">{len(entry["sense_candidates"])} senses?</span>')
        if entry.get("cross_references"):
            badges.append(f'<span class="metric">{len(entry["cross_references"])} cross-refs</span>')
        if len(entry.get("forms", [])) > 1:
            badges.append(f'<span class="metric">{len(entry["forms"]) - 1} derivatives</span>')
        if entry.get("partial_start") or entry.get("partial_end"):
            badges.append('<span class="metric danger">partial selection</span>')

        search_text = " ".join(
            [title, page_text, " ".join(labels)]
            + [line_lookup[line_id]["normalized_text"] for line_id in entry["line_refs"]]
        ).lower()
        first_page = pages[0] if pages else ""
        pdf_href = f"../acomprehensive.pdf#page={first_page}" if first_page else "../acomprehensive.pdf"
        cards.append(
            "\n".join(
                [
                    f'<article class="entry" id="{html.escape(entry["entry_id"], quote=True)}" '
                    f'data-page="{first_page}" data-issues="{" ".join(issue_tokens)}" '
                    f'data-search="{html.escape(search_text, quote=True)}">',
                    '  <header class="entry-header">',
                    f'    <h2>{html.escape(title)}</h2>',
                    '    <div class="header-actions">',
                    f'      <a class="page-link" href="{html.escape(pdf_href, quote=True)}">PDF {html.escape(page_text)}</a>',
                    f'      <label class="flag-label"><input class="flag" type="checkbox" data-entry="{html.escape(entry["entry_id"], quote=True)}"> flag</label>',
                    "    </div>",
                    "  </header>",
                    f'  <div class="badges">{"".join(badges)}</div>',
                    f'  <div class="entry-lines">{"".join(line_html)}</div>',
                    "</article>",
                ]
            )
        )

    page_options = ['<option value="">All pages</option>'] + [
        f'<option value="{page}">PDF page {page}</option>' for page in selected_pages
    ]
    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>A Comprehensive Dictionary — agent0 pilot review</title>
<style>
:root {{
  color-scheme: light dark;
  --bg: #f5f6f8; --panel: #fff; --text: #1d232a; --muted: #66717e;
  --line: #d8dde4; --accent: #3386bf; --warn: #d9a14a; --danger: #d92626;
}}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#15181d; --panel:#20252b; --text:#edf1f5; --muted:#aab4bf; --line:#3a424c; }}
}}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.45 system-ui,sans-serif; }}
.toolbar {{
  position:sticky; top:0; z-index:10; display:grid; grid-template-columns:minmax(16rem,2fr) repeat(3,minmax(9rem,1fr)) auto;
  gap:.55rem; padding:.7rem; background:color-mix(in srgb,var(--panel) 94%,transparent);
  border-bottom:1px solid var(--line); backdrop-filter:blur(10px);
}}
input,select,button {{ font:inherit; padding:.48rem .58rem; border:1px solid var(--line); border-radius:.35rem; background:var(--panel); color:var(--text); }}
button {{ cursor:pointer; }}
.status {{ align-self:center; color:var(--muted); white-space:nowrap; }}
main {{ max-width:1120px; margin:0 auto; padding:1rem; }}
.help {{ color:var(--muted); margin:.1rem 0 1rem; }}
.entry {{ background:var(--panel); border:1px solid var(--line); border-radius:.55rem; padding:.8rem; margin:0 0 .75rem; scroll-margin-top:5.5rem; }}
.entry.active {{ outline:3px solid color-mix(in srgb,var(--accent) 55%,transparent); }}
.entry.flagged {{ border-left:6px solid var(--warn); }}
.entry-header {{ display:flex; align-items:center; justify-content:space-between; gap:1rem; }}
h2 {{ font-size:1.18rem; margin:0; }}
.header-actions {{ display:flex; align-items:center; gap:.8rem; white-space:nowrap; }}
.page-link {{ color:var(--accent); }}
.flag-label {{ color:var(--muted); }}
.badges {{ display:flex; flex-wrap:wrap; gap:.3rem; margin:.45rem 0; min-height:.2rem; }}
.tag,.metric {{ display:inline-block; border-radius:.28rem; padding:.08rem .32rem; font-size:.75rem; font-weight:700; }}
.tag {{ color:white; background:var(--tag-color,#626273); }}
.metric {{ background:color-mix(in srgb,var(--accent) 18%,transparent); color:var(--text); }}
.metric.warn {{ background:color-mix(in srgb,var(--warn) 30%,transparent); }}
.metric.danger {{ background:color-mix(in srgb,var(--danger) 28%,transparent); }}
.entry-lines {{ border-top:1px solid var(--line); padding-top:.5rem; max-height:14rem; overflow:auto; font-family:Georgia,"Times New Roman",serif; }}
body.expanded .entry-lines {{ max-height:none; }}
.source-line {{ min-height:1.1em; padding-left:.4rem; }}
.bold {{ font-weight:700; }}
.italic {{ font-style:italic; }}
.xref {{ font-variant:small-caps; font-weight:700; color:var(--accent); }}
.hidden {{ display:none !important; }}
@media (max-width:850px) {{
  .toolbar {{ grid-template-columns:1fr 1fr; }}
  .entry-header {{ align-items:flex-start; }}
}}
</style>
</head>
<body>
<div class="toolbar">
  <input id="search" type="search" placeholder="Search headword or entry text…">
  <select id="pageFilter">{"".join(page_options)}</select>
  <select id="issueFilter">
    <option value="">All entries</option>
    <option value="unclassified">Unresolved italics</option>
    <option value="partial">Partial selections</option>
    <option value="multiple-forms">Has derivatives</option>
    <option value="flagged">Flagged by me</option>
  </select>
  <select id="limit">
    <option value="50">Show 50</option>
    <option value="100" selected>Show 100</option>
    <option value="250">Show 250</option>
    <option value="99999">Show all</option>
  </select>
  <button id="expand" type="button">Expand lines</button>
  <span class="status" id="status"></span>
</div>
<main>
  <p class="help">J/K: next/previous visible entry · F: flag active entry · Enter in search: jump to first result. Flags persist in this browser.</p>
  <div id="entries">
{"".join(cards)}
  </div>
</main>
<script>
(() => {{
  const cards = [...document.querySelectorAll('.entry')];
  const search = document.querySelector('#search');
  const pageFilter = document.querySelector('#pageFilter');
  const issueFilter = document.querySelector('#issueFilter');
  const limit = document.querySelector('#limit');
  const status = document.querySelector('#status');
  const storageKey = 'acomprehensive-agent0-review-flags';
  let flags = new Set(JSON.parse(localStorage.getItem(storageKey) || '[]'));
  let visible = [];
  let activeIndex = -1;

  function applyFlags() {{
    cards.forEach(card => {{
      const checkbox = card.querySelector('.flag');
      checkbox.checked = flags.has(card.id);
      card.classList.toggle('flagged', checkbox.checked);
    }});
  }}
  function saveFlags() {{
    localStorage.setItem(storageKey, JSON.stringify([...flags]));
  }}
  function refilter() {{
    const q = search.value.trim().toLowerCase();
    const page = pageFilter.value;
    const issue = issueFilter.value;
    const max = Number(limit.value);
    let shown = 0;
    visible = [];
    cards.forEach(card => {{
      let match = (!q || card.dataset.search.includes(q)) && (!page || card.dataset.page === page);
      if (issue === 'flagged') match = match && flags.has(card.id);
      else if (issue) match = match && card.dataset.issues.split(' ').includes(issue);
      if (match && shown < max) {{ card.classList.remove('hidden'); visible.push(card); shown++; }}
      else card.classList.add('hidden');
      card.classList.remove('active');
    }});
    activeIndex = visible.length ? 0 : -1;
    if (activeIndex >= 0) visible[0].classList.add('active');
    status.textContent = `${{visible.length}} shown · ${{cards.length}} total · ${{flags.size}} flagged`;
  }}
  function activate(index, scroll=true) {{
    if (!visible.length) return;
    activeIndex = Math.max(0, Math.min(index, visible.length - 1));
    cards.forEach(card => card.classList.remove('active'));
    visible[activeIndex].classList.add('active');
    if (scroll) visible[activeIndex].scrollIntoView({{block:'center', behavior:'smooth'}});
  }}
  document.querySelectorAll('.flag').forEach(box => box.addEventListener('change', e => {{
    const id = e.target.dataset.entry;
    if (e.target.checked) flags.add(id); else flags.delete(id);
    saveFlags(); applyFlags(); refilter();
  }}));
  [search,pageFilter,issueFilter,limit].forEach(el => el.addEventListener(el === search ? 'input' : 'change', refilter));
  document.querySelector('#expand').addEventListener('click', e => {{
    document.body.classList.toggle('expanded');
    e.target.textContent = document.body.classList.contains('expanded') ? 'Compact lines' : 'Expand lines';
  }});
  document.addEventListener('keydown', e => {{
    if (e.target.matches('input,select')) {{
      if (e.key === 'Enter' && e.target === search) activate(0);
      return;
    }}
    if (e.key.toLowerCase() === 'j') activate(activeIndex + 1);
    if (e.key.toLowerCase() === 'k') activate(activeIndex - 1);
    if (e.key.toLowerCase() === 'f' && activeIndex >= 0) {{
      const box = visible[activeIndex].querySelector('.flag');
      box.checked = !box.checked; box.dispatchEvent(new Event('change', {{bubbles:true}}));
    }}
  }});
  applyFlags();
  refilter();
}})();
</script>
</body>
</html>
"""
    path = output_dir / "pilot_review.html"
    path.write_text(document, encoding="utf-8")
    return path


def build_scan_html(output_dir: Path, entries: list[dict[str, Any]]) -> Path:
    expressions = []
    for entry in entries:
        if entry.get("headword_candidate"):
            expressions.append(normalize_lookup(entry["headword_candidate"]))
        for form in entry.get("forms", []):
            if form.get("form_kind") == "derivative" and form.get("expression_candidate"):
                expressions.append(normalize_lookup(form["expression_candidate"]))
    expressions = list(dict.fromkeys(expressions))
    random.Random(20260630).shuffle(expressions)
    paragraphs = []
    for start in range(0, len(expressions), 60):
        words = " · ".join(html.escape(word) for word in expressions[start : start + 60])
        paragraphs.append(f"<p>{words}</p>")
    path = output_dir / "pilot_scan.html"
    path.write_text(
        """<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Yomitan scan text — A Comprehensive pilot</title>
<style>
body{max-width:1000px;margin:2rem auto;padding:0 1rem;font:24px/2 Georgia,serif}
p{margin:0 0 2rem}
</style>
</head>
<body>
<h1>Yomitan scan text</h1>
"""
        + "\n".join(paragraphs)
        + "\n</body>\n</html>\n",
        encoding="utf-8",
    )
    return path


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=script_dir / "acomprehensive.pdf")
    parser.add_argument("--output", type=Path, default=script_dir / "pilot_agent0")
    parser.add_argument("--pages", default=DEFAULT_PAGES)
    parser.add_argument("--tag-map", type=Path, default=script_dir / "acomprehensive_tags_map.csv")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    obsolete_outputs = ["raw_pages.jsonl", "entries.jsonl"]
    for obsolete_name in obsolete_outputs:
        obsolete_path = args.output / obsolete_name
        if obsolete_path.exists():
            obsolete_path.unlink()
    tag_map = load_tag_map(args.tag_map)
    doc = pymupdf.open(args.pdf)
    selected_pages = parse_page_spec(args.pages, doc.page_count)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "script_version": SCRIPT_VERSION,
        "extractor": "PyMuPDF",
        "extractor_version": pymupdf.__version__,
        "pdf_path": str(args.pdf.resolve()),
        "pdf_sha256": sha256_file(args.pdf),
        "pdf_size_bytes": args.pdf.stat().st_size,
        "pdf_page_count": doc.page_count,
        "selected_pdf_pages": selected_pages,
        "semantic_layer": "intermediate/entries/*.json",
        "form_index": "intermediate/index.csv",
        "manual_overrides": "intermediate/overrides.csv",
        "tag_map": str(args.tag_map.resolve()),
        "tag_map_sha256": sha256_file(args.tag_map),
        "normalization_rules": [
            {"raw": raw, "codepoint": f"U+{ord(raw):04X}", "replacement": value, "reason": reason}
            for raw, (value, reason) in CHAR_NORMALIZATION.items()
        ],
        "lookup_normalization": {
            "method": "Unicode NFKD accent folding plus explicit Latin transliterations",
            "scope": "headwords, forms, aliases, scan text, and internal-link targets only",
            "preserved_fields": "PDF spelling and definition/example content retain accents",
        },
        "losslessness_contract": [
            "Semantic content preserves PDF spelling, source order, and bold/italic/label/cross-reference roles.",
            "Extraction repairs retain original extracted text and repair events on affected semantic text nodes.",
            "Lookup normalization is stored beside, never in place of, PDF spelling.",
            "Unclassified italics remain italic and are listed in italic_annotations.",
            "The canonical PDF and recorded SHA-256 remain the final visual authority.",
        ],
        "obsolete_outputs_removed": obsolete_outputs,
    }

    page_lines: dict[int, list[dict[str, Any]]] = {}
    line_lookup: dict[str, dict[str, Any]] = {}
    for page_number in selected_pages:
        _page_obj, lines = serialize_page(doc[page_number - 1], page_number)
        page_lines[page_number] = lines
        line_lookup.update({line["line_id"]: line for line in lines})

    entries, metrics = build_entries(selected_pages, page_lines, tag_map)
    semantic_entries = build_semantic_entries(entries, line_lookup, tag_map)
    semantic_output = write_semantic_intermediate(args.output, semantic_entries)

    character_audit = full_character_audit(doc)
    (args.output / "character_audit.json").write_text(
        json_dump(character_audit, indent=2) + "\n", encoding="utf-8"
    )
    rendered = render_representative_pages(doc, args.output, selected_pages)
    zip_path, term_count = build_yomitan(args.output, entries, line_lookup, tag_map)
    scan_path = build_scan_html(args.output, entries)
    manifest["pilot_metrics"] = metrics
    manifest["pilot_entry_records"] = len(entries)
    manifest["pilot_yomitan_terms"] = term_count
    manifest["semantic_output"] = semantic_output
    manifest["representative_renders"] = rendered
    manifest["pilot_zip"] = zip_path.name
    manifest["pilot_scan_html"] = scan_path.name
    (args.output / "manifest.json").write_text(json_dump(manifest, indent=2) + "\n", encoding="utf-8")

    summary = {
        "selected_pages": selected_pages,
        "entry_records": len(entries),
        "yomitan_terms": term_count,
        "metrics": metrics,
        "files": {
            "manifest": "manifest.json",
            "semantic_entries": semantic_output["entry_chunks"],
            "form_index": semantic_output["index"],
            "manual_overrides": semantic_output["overrides"],
            "character_audit": "character_audit.json",
            "pilot_zip": zip_path.name,
            "scan_html": scan_path.name,
        },
    }
    (args.output / "pilot_summary.json").write_text(json_dump(summary, indent=2) + "\n", encoding="utf-8")
    print(json_dump(summary, indent=2))


if __name__ == "__main__":
    main()
