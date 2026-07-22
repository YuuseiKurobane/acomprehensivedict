#!/usr/bin/env python3
"""Layered PDF extraction pipeline for A Comprehensive Indonesian-English Dictionary.

Data flow:

    PDF -> lightweight debug JSON -> human marker text -> Yomitan

The human marker text is the only lexical input accepted by the Yomitan builder.
The debug JSON retains page/line/font/repair evidence, but is not a generation
source.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

try:
    import pymupdf
except ImportError:  # Human intermediate -> Yomitan does not require PyMuPDF.
    pymupdf = None


DEBUG_SCHEMA_VERSION = "acomprehensive-debug-1"
HUMAN_SCHEMA_VERSION = "acomprehensive-markers-1"
SCRIPT_VERSION = "1.3.0"
DEFAULT_PAGES = "21-30,70,236,500"

# Verified visually by Agent 0. U+00AD is handled specially at line boundaries.
CHAR_NORMALIZATION = {
    "\u00a4": ("fi", "legacy Indrev font ligature; rendered glyph is fi"),
    "\u00b6": ("fl", "legacy Indrev font ligature; rendered glyph is fl"),
    "\u00d8": ("1/5", "legacy fraction glyph; rendered glyph is 1/5"),
    "\u2030": ("1/6", "legacy fraction glyph; rendered glyph is 1/6"),
    "\u00b3": ("1/3", "legacy fraction glyph; rendered glyph is 1/3"),
    "\u00bf": ("1/12", "legacy fraction glyph; rendered glyph is 1/12"),
    "\u00ad": ("", "discretionary soft hyphen at a visual line break"),
}

BOLD_FONTS = {"Indrev-Bold", "Indrev-BoldItalic"}
ITALIC_FONTS = {"Indrev-Italic", "Indrev-BoldItalic", "Indrev-Italic-SC700"}
SMALL_CAPS_FONTS = {"Inresc-Roman", "Indrev-Roman-SC700", "Indrev-Bold-SC700"}
ROOT_X = (42.0, 258.0)
DERIVATIVE_X = (48.0, 264.0)
BODY_Y_MIN = 50.0
BODY_Y_MAX = 690.0

# The source PDF contains a finite set of reviewed font-run mistakes at
# lexical starts.  Most lose the first letter's bold style; four lose the
# final letter instead, and seven complete headwords are Roman.  Repairing the
# style here lets the ordinary geometry/classification grammar handle them
# without turning every Roman line at an anchor into a form (there is a known
# legitimate counterexample in the NW entry).
REVIEWED_HEADWORD_RUN_REPAIRS: dict[str, dict[str, Any]] = {
    "p0048-l0077": {"headword": "pengamanat"},
    "p0057-l0128": {"headword": "ngangetin"},
    "p0081-l0137": {"headword": "berasyo(o)i"},
    "p0229-l0003": {"headword": "(pe)combéran"},
    "p0272-l0110": {"headword": "kedudukan"},
    "p0420-l0143": {"headword": "menjadwalkan"},
    "p0446-l0071": {"headword": "menjotoskan"},
    "p0447-l0064": {"headword": "menjuang"},
    "p0447-l0067": {"headword": "kejuangan"},
    "p0447-l0096": {"headword": "pejuaraan"},
    "p0584-l0141": {"headword": "layak I"},
    "p0635-l0073": {"headword": "kemanisan"},
    "p0762-l0070": {"headword": "mempermak"},
    "p0839-l0135": {
        "headword": "rélevir",
        "variants": ["merélevir"],
    },
    "p0945-l0091": {"headword": "berseru"},
    "p0968-l0007": {"headword": "berskala"},
    "p0973-l0073": {"headword": "kesoréan"},
    "p1036-l0033": {"headword": "témpong III"},
    "p1041-l0009": {"headword": "bertepekur"},
    "p1049-l0134": {"headword": "bertimbalan"},
}

ROMAN_TOKEN_RE = re.compile(r"(?<!\S)([IVXLCDM]+)(?!\S)")
SENSE_RE = re.compile(r"^\s*(\d+)\s*$")
MARKER_RE = re.compile(r"^\[([^\]]+)\](?:\s(.*))?$")
BINOMIAL_RE = re.compile(r"^[A-Z][a-zéë-]+(?:\s+[a-zéë-]+){1,2}[,.]?$")
OPTIONAL_GROUP_RE = re.compile(r"\(([^()]*)\)")
AFFIXED_ACRONYM_RE = re.compile(r"^(.+?)-([A-Z][A-Z0-9]+)-(.+)$")


def json_dump(value: Any, *, indent: int | None = None) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=indent,
        separators=None if indent else (",", ":"),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_page_spec(spec: str, page_count: int) -> list[int]:
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            first, last = part.split("-", 1)
            pages.update(range(int(first), int(last) + 1))
        else:
            pages.add(int(part))
    selected = sorted(page for page in pages if 1 <= page <= page_count)
    if not selected:
        raise ValueError("Page selection is empty.")
    return selected


def normalize_extracted_text(text: str) -> tuple[str, list[dict[str, Any]]]:
    output: list[str] = []
    repairs: list[dict[str, Any]] = []
    for offset, character in enumerate(text):
        replacement = CHAR_NORMALIZATION.get(character)
        if replacement is None:
            output.append(character)
            continue
        value, reason = replacement
        output.append(value)
        repairs.append(
            {
                "raw_offset": offset,
                "raw": character,
                "codepoint": f"U+{ord(character):04X}",
                "replacement": value,
                "reason": reason,
            }
        )
    return "".join(output), repairs


def normalize_lookup(text: str) -> str:
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


def clean_text(text: str) -> str:
    """Collapse visual-layout whitespace while retaining lexical punctuation."""
    text = text.replace("\t", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.;:!?%\]])", r"\1", text)
    text = re.sub(r"([\[(])\s+", r"\1", text)
    return text


def join_piece(buffer: str, piece: str, boundary: str = "") -> str:
    if not piece:
        return buffer
    if not buffer:
        return piece.lstrip()
    if boundary == "":
        if buffer[-1:].isspace() or piece[:1].isspace():
            return buffer + piece
        return buffer + piece
    if buffer[-1:].isspace() or piece[:1].isspace():
        return buffer + piece
    return buffer + boundary + piece


def near(value: float, targets: Iterable[float], tolerance: float = 1.25) -> bool:
    return any(abs(value - target) <= tolerance for target in targets)


def load_tag_map(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return {row["tag"]: row for row in csv.DictReader(stream)}


def tag_codes(text: str, tag_map: dict[str, dict[str, str]]) -> list[str]:
    candidate = text.strip().strip("()[]").strip()
    if candidate in tag_map:
        return [candidate]
    pieces = candidate.split()
    if pieces and all(piece in tag_map for piece in pieces):
        return pieces
    return []


def span_style(font: str) -> str:
    if font in SMALL_CAPS_FONTS:
        return "small_caps"
    if font == "SymbolMT":
        return "symbol"
    if font == "Indrev-BoldItalic":
        return "bold_italic"
    if font in BOLD_FONTS:
        return "bold"
    if font in ITALIC_FONTS:
        return "italic"
    return "roman"


def extract_page_lines(page: Any, page_number: int) -> list[dict[str, Any]]:
    """Extract lightweight line/span evidence without retaining character geometry."""
    raw = page.get_text("rawdict")
    lines: list[dict[str, Any]] = []
    serial = 0
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for source_line in block.get("lines", []):
            serial += 1
            spans = []
            raw_line_parts = []
            clean_line_parts = []
            for source_span in source_line.get("spans", []):
                raw_text = "".join(char.get("c", "") for char in source_span.get("chars", []))
                normalized, repairs = normalize_extracted_text(raw_text)
                font = source_span.get("font", "")
                span = {
                    "font": font,
                    "style": span_style(font),
                    "bbox": [round(float(value), 4) for value in source_span.get("bbox", ())],
                    "raw_text": raw_text,
                    "clean_text": normalized,
                }
                if repairs:
                    span["repairs"] = repairs
                spans.append(span)
                raw_line_parts.append(raw_text)
                clean_line_parts.append(normalized)
            bbox = [round(float(value), 4) for value in source_line.get("bbox", ())]
            raw_text = "".join(raw_line_parts)
            line = {
                "line_id": f"p{page_number:04d}-l{serial:04d}",
                "pdf_page": page_number,
                "bbox": bbox,
                "column": 0 if bbox[0] < page.rect.width / 2 else 1,
                "raw_text": raw_text,
                "clean_text": "".join(clean_line_parts),
                "join_next_without_space": raw_text.rstrip().endswith("\u00ad"),
                "spans": spans,
            }
            _apply_reviewed_headword_run_repair(line)
            lines.append(line)
    lines.sort(key=lambda line: (line["column"], round(line["bbox"][1], 1), line["bbox"][0]))
    return lines


def _apply_reviewed_headword_run_repair(line: dict[str, Any]) -> None:
    """Restore reviewed headword boldness while retaining source evidence."""
    repair = REVIEWED_HEADWORD_RUN_REPAIRS.get(str(line["line_id"]))
    if repair is None:
        return

    headword = str(repair["headword"])
    full_text = "".join(str(span["clean_text"]) for span in line["spans"])
    start = len(full_text) - len(full_text.lstrip())
    if full_text[start : start + len(headword)] != headword:
        raise ValueError(
            f"Reviewed headword evidence changed for {line['line_id']}: "
            f"expected {headword!r}, got {full_text[start:start + len(headword)]!r}"
        )
    end = start + len(headword)

    repaired_spans: list[dict[str, Any]] = []
    offset = 0
    for span in line["spans"]:
        text = str(span["clean_text"])
        span_start = offset
        span_end = offset + len(text)
        offset = span_end
        cuts = sorted({span_start, span_end, max(span_start, start), min(span_end, end)})
        cuts = [value for value in cuts if span_start <= value <= span_end]
        for piece_start, piece_end in zip(cuts, cuts[1:]):
            if piece_start == piece_end:
                continue
            local_start = piece_start - span_start
            local_end = piece_end - span_start
            piece = {**span, "clean_text": text[local_start:local_end]}
            raw_text = str(span.get("raw_text", ""))
            if len(raw_text) == len(text):
                piece["raw_text"] = raw_text[local_start:local_end]
            if span_end > span_start:
                x0, y0, x1, y1 = (float(value) for value in span["bbox"])
                width = x1 - x0
                piece["bbox"] = [
                    round(x0 + width * local_start / len(text), 4),
                    y0,
                    round(x0 + width * local_end / len(text), 4),
                    y1,
                ]
            if piece_start < end and piece_end > start:
                piece["style"] = "bold"
                piece["reviewed_style_repair"] = True
            repaired_spans.append(piece)

    line["spans"] = repaired_spans
    line["reviewed_headword"] = headword
    if repair.get("variants"):
        line["reviewed_variants"] = list(repair["variants"])


def meaningful_spans(line: dict[str, Any]) -> list[dict[str, Any]]:
    return [span for span in line["spans"] if span["clean_text"].strip()]


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
    if first["style"] in {"bold", "bold_italic"} and near(x, ROOT_X):
        return "root_entry_start"
    if first["style"] in {"bold", "bold_italic"} and near(x, DERIVATIVE_X):
        return "subentry_start"
    return "continuation"


def group_debug_entries(
    pdf_path: Path,
    selected_pages: list[int],
) -> list[dict[str, Any]]:
    if pymupdf is None:
        raise RuntimeError("PyMuPDF is required for PDF extraction.")
    document = pymupdf.open(pdf_path)
    entries: list[dict[str, Any]] = []
    current_entry: dict[str, Any] | None = None
    current_form: dict[str, Any] | None = None
    previous_page: int | None = None

    def close_current(*, partial_end: bool) -> None:
        nonlocal current_entry, current_form
        if current_entry is not None:
            current_entry["source"]["partial_end"] = partial_end
            current_entry["source"]["pdf_pages"] = sorted(
                set(current_entry["source"]["pdf_pages"])
            )
            entries.append(current_entry)
        current_entry = None
        current_form = None

    for page_number in selected_pages:
        if previous_page is not None and page_number != previous_page + 1:
            close_current(partial_end=True)
        previous_page = page_number
        for line in extract_page_lines(document[page_number - 1], page_number):
            line_class = classify_line(line)
            line["line_class"] = line_class
            if line_class in {"running_header", "running_footer", "blank"}:
                continue
            if line_class == "root_entry_start":
                close_current(partial_end=False)
                current_entry = {
                    "entry_type": "root",
                    "source": {
                        "pdf_pages": [page_number],
                        "partial_start": False,
                        "partial_end": False,
                    },
                    "forms": [],
                    "warnings": [],
                }
                current_form = {"form_type": "root", "lines": []}
                current_entry["forms"].append(current_form)
            elif line_class == "subentry_start" and current_entry is not None:
                current_form = {"form_type": "subentry", "lines": []}
                current_entry["forms"].append(current_form)
            elif current_entry is None:
                current_entry = {
                    "entry_type": "orphan_fragment",
                    "source": {
                        "pdf_pages": [page_number],
                        "partial_start": True,
                        "partial_end": False,
                    },
                    "forms": [],
                    "warnings": ["Page selection begins inside an entry."],
                }
                current_form = {"form_type": "orphan", "lines": []}
                current_entry["forms"].append(current_form)
            assert current_entry is not None
            assert current_form is not None
            current_entry["source"]["pdf_pages"].append(page_number)
            current_form["lines"].append(line)
    close_current(partial_end=True)
    document.close()
    return entries


def _next_meaningful_span(spans: list[dict[str, Any]], start: int) -> tuple[int, dict[str, Any]] | None:
    for index in range(start, len(spans)):
        if spans[index]["clean_text"].strip():
            return index, spans[index]
    return None


def _connector_allows_bold(text: str) -> bool:
    stripped = clean_text(text).lower()
    stripped = stripped.strip("[]()")
    if not stripped:
        return True
    return bool(
        re.fullmatch(
            r"(?:and|or|and/or|/|&|,|;|:|\.|,\s*(?:and|or))",
            stripped,
        )
    )


def leading_expression_zone(line: dict[str, Any]) -> dict[str, Any]:
    """Collect a leading expression zone across cooperating bold/roman spans."""
    spans = line["spans"]
    start = next(
        (
            index
            for index, span in enumerate(spans)
            if span["style"] in {"bold", "bold_italic"} and span["clean_text"].strip()
        ),
        None,
    )
    if start is None:
        return {
            "start": 0,
            "end": 0,
            "text": "",
            "bold_chunks": [],
            "remaining_spans": spans,
            "warnings": ["No leading bold expression was found."],
        }

    included_end = start + 1
    bold_chunks = [spans[start]["clean_text"]]
    cursor = start + 1
    connector_seen = False
    while cursor < len(spans):
        next_item = _next_meaningful_span(spans, cursor)
        if next_item is None:
            break
        next_index, next_span = next_item
        between_text = "".join(span["clean_text"] for span in spans[cursor:next_index])
        if next_span["style"] not in {"bold", "bold_italic"}:
            if next_span["style"] != "roman":
                break
            following = _next_meaningful_span(spans, next_index + 1)
            if following is None or following[1]["style"] not in {"bold", "bold_italic"}:
                break
            connector_text = between_text + next_span["clean_text"]
            following_text = following[1]["clean_text"].strip()
            is_explicit_connector = bool(clean_text(connector_text).strip())
            is_roman_structural = bool(
                re.match(r"^[IVXLCDM]+(?:\s|/|$)", following_text)
            )
            if not _connector_allows_bold(connector_text):
                break
            if not (is_explicit_connector or connector_seen or is_roman_structural):
                break
            connector_seen = connector_seen or is_explicit_connector
            included_end = following[0] + 1
            bold_chunks.append(following[1]["clean_text"])
            cursor = following[0] + 1
            continue

        # Adjacent bold spans can be font fragmentation or a structural Roman token.
        candidate = next_span["clean_text"].strip()
        if SENSE_RE.fullmatch(candidate):
            break
        included_end = next_index + 1
        bold_chunks.append(next_span["clean_text"])
        cursor = next_index + 1

    zone_text = "".join(span["clean_text"] for span in spans[start:included_end])
    return {
        "start": start,
        "end": included_end,
        "text": clean_text(zone_text),
        "bold_chunks": [clean_text(chunk) for chunk in bold_chunks if clean_text(chunk)],
        "remaining_spans": spans[included_end:],
        "warnings": [],
    }


def _valid_roman(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})",
            value,
        )
    )


def _strip_trailing_structures(text: str) -> tuple[str, int | None, str | None]:
    sense: int | None = None
    operator: str | None = None
    changed = True
    while changed:
        changed = False
        sense_match = re.search(r"\s+(\d+)\s*$", text)
        if sense_match and sense is None:
            sense = int(sense_match.group(1))
            text = text[: sense_match.start()].rstrip()
            changed = True
            continue
        operator_match = re.search(r"\s*([–~])\s*$", text)
        if operator_match and operator is None:
            operator = operator_match.group(1)
            text = text[: operator_match.start()].rstrip()
            changed = True
    return text, sense, operator


def _split_expression_variants(text: str, multi_span: bool) -> list[str]:
    text = clean_text(text)
    text = re.sub(
        r"\[\s*(and/or|and|or)\s+",
        r" \1 ",
        text,
        flags=re.I,
    )
    text = text.strip(" []")
    if not text:
        return []
    if multi_span or re.search(r"\s+(?:and|or|and/or)\s+", text, flags=re.I):
        pieces = re.split(r"\s+(?:and|or|and/or)\s+|\s+/\s+", text, flags=re.I)
    else:
        pieces = [text]
    return [clean_text(piece).strip(" []") for piece in pieces if clean_text(piece).strip(" []")]


def parse_expression_zone(zone: dict[str, Any], following_text: str = "") -> dict[str, Any]:
    text = zone["text"]
    pronunciation_prefix = ""
    if text.endswith("/") and re.match(r"^\s*[^/]+/", following_text):
        text = text[:-1].rstrip()
        pronunciation_prefix = "/"

    text, sense, operator = _strip_trailing_structures(text)
    homograph = None
    inline_subentry = None
    expression_part = text
    for match in ROMAN_TOKEN_RE.finditer(text):
        if not _valid_roman(match.group(1)):
            continue
        homograph = match.group(1)
        expression_part = text[: match.start()].rstrip()
        suffix = text[match.end() :].strip()
        suffix, suffix_sense, suffix_operator = _strip_trailing_structures(suffix)
        if suffix_sense is not None:
            sense = suffix_sense
        if suffix_operator is not None:
            operator = suffix_operator
        inline_subentry = suffix.strip(" []") or None
        break

    variants = _split_expression_variants(
        expression_part,
        multi_span=len(zone["bold_chunks"]) > 1,
    )
    primary = variants[0] if variants else clean_text(expression_part)
    return {
        "expression": primary,
        "variants": variants[1:],
        "homograph": homograph,
        "initial_sense": sense,
        "inline_subentry": inline_subentry,
        "template_operator": operator,
        "pronunciation_prefix": pronunciation_prefix,
        "source_zone": zone["text"],
        "warnings": zone["warnings"] + ([] if primary else ["Expression zone parsed empty."]),
    }


def _span_event(
    span: dict[str, Any],
    tag_map: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    text = span["clean_text"]
    stripped = text.strip()
    if not stripped:
        if text:
            return [
                {
                    "kind": "text",
                    "value": text,
                    "style": "roman",
                    "boundary": "",
                }
            ]
        return []
    codes = tag_codes(stripped, tag_map) if span["style"] in {"italic", "bold_italic"} else []
    if codes:
        return [{"kind": "label", "value": code, "boundary": ""} for code in codes]
    if span["style"] == "small_caps":
        return [{"kind": "xref", "value": stripped, "boundary": ""}]
    if span["style"] == "symbol" and "→" in text:
        return [{"kind": "arrow", "value": "→", "boundary": ""}]
    if span["style"] in {"bold", "bold_italic"} and SENSE_RE.fullmatch(text):
        return [{"kind": "sense", "value": int(stripped), "boundary": ""}]
    return [
        {
            "kind": "text",
            "value": text,
            "style": span["style"],
            "boundary": "",
        }
    ]


def content_events(
    form: dict[str, Any],
    zone: dict[str, Any],
    parsed_zone: dict[str, Any],
    tag_map: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    lines = form["lines"]
    events: list[dict[str, Any]] = []
    for line_index, line in enumerate(lines):
        spans = zone["remaining_spans"] if line_index == 0 else line["spans"]
        first_event_on_line = True
        boundary = ""
        if line_index:
            boundary = "" if lines[line_index - 1]["join_next_without_space"] else " "
        for span in spans:
            for event in _span_event(span, tag_map):
                if first_event_on_line:
                    event["boundary"] = boundary
                    first_event_on_line = False
                events.append(event)
    prefix = parsed_zone["pronunciation_prefix"]
    if prefix:
        events.insert(
            0,
            {
                "kind": "text",
                "value": prefix,
                "style": "roman",
                "boundary": "",
            },
        )
    return events


def _trim_label_wrappers(events: list[dict[str, Any]]) -> None:
    """Remove punctuation whose only job was to wrap a parsed source label."""
    for index, event in enumerate(events):
        if event["kind"] != "label":
            continue
        if index:
            previous = events[index - 1]
            if previous["kind"] == "text":
                previous["value"] = re.sub(r"[\s(\[]+$", " ", previous["value"])
        if index + 1 < len(events):
            following = events[index + 1]
            if following["kind"] == "text":
                following["value"] = re.sub(r"^[\s)\]]+", " ", following["value"])


def _extract_prefix_metadata(
    events: list[dict[str, Any]],
) -> tuple[str | None, str | None]:
    pronunciation = None
    expansion = None
    text_indexes = [index for index, event in enumerate(events) if event["kind"] == "text"]
    if not text_indexes:
        return pronunciation, expansion

    # Pronunciations can be split at a font boundary: bold "/" + roman "a/".
    combined = ""
    used: list[int] = []
    for index in text_indexes[:3]:
        if events[index]["kind"] != "text":
            continue
        combined += events[index]["value"]
        used.append(index)
        match = re.match(r"^\s*(/[^/]+/)\s*(.*)$", combined, flags=re.S)
        if match:
            pronunciation = clean_text(match.group(1))
            remainder = match.group(2)
            for used_index in used:
                events[used_index]["value"] = ""
            events[used[-1]]["value"] = remainder
            events.insert(
                used[0],
                {
                    "kind": "pronunciation",
                    "value": pronunciation,
                    "boundary": "",
                },
            )
            break

    first_text = next(
        (
            (index, event)
            for index, event in enumerate(events)
            if event["kind"] == "text" and event["value"].strip()
        ),
        None,
    )
    if first_text is not None:
        index, event = first_text
        match = re.match(r"^\s*\[([^\]]+)\]\s*(.*)$", event["value"], flags=re.S)
        if match:
            expansion = clean_text(match.group(1))
            event["value"] = match.group(2)
            events.insert(
                index,
                {
                    "kind": "expansion",
                    "value": expansion,
                    "boundary": "",
                },
            )
    return pronunciation, expansion


def _move_example_punctuation(example: str, translation: str) -> tuple[str, str]:
    match = re.match(r"^\s*([.;!?])\s*(.*)$", translation, flags=re.S)
    if match:
        example = example.rstrip() + match.group(1)
        translation = match.group(2)
    return example, translation


def resolve_example_placeholders(
    text: str,
    root_expression: str,
    current_expression: str,
) -> str:
    """Resolve source notation while rendering Layer 2 output."""
    text = re.sub(r"(?<!\w)–(?!\w)", root_expression, text)
    text = text.replace("~", current_expression)
    return clean_text(text)


def parse_form_content(
    events: list[dict[str, Any]],
    *,
    initial_sense: int | None,
    template_operator: str | None,
    root_expression: str,
    current_expression: str,
) -> dict[str, Any]:
    _trim_label_wrappers(events)
    pronunciation, expansion = _extract_prefix_metadata(events)
    labels: list[str] = []
    output: list[dict[str, Any]] = []
    definition = ""
    example = f"{template_operator} " if template_operator else ""
    translation = ""
    in_example = bool(template_operator)
    current_sense = initial_sense
    waiting_for_xref = False

    def flush_definition() -> None:
        nonlocal definition
        value = clean_text(definition).strip(" )]")
        if value:
            output.append({"type": "definition", "number": current_sense, "value": value})
        definition = ""

    def flush_example() -> None:
        nonlocal example, translation, in_example
        example_value = clean_text(example)
        translation_value = clean_text(translation)
        example_value, translation_value = _move_example_punctuation(
            example_value,
            translation_value,
        )
        if example_value:
            output.append(
                {
                    "type": "example",
                    "value": example_value,
                }
            )
        if translation_value:
            output.append({"type": "translation", "value": translation_value})
        example = ""
        translation = ""
        in_example = False

    def flush_example_value() -> None:
        """Emit an example while keeping its translation context open."""
        nonlocal example
        example_value = clean_text(example)
        if example_value:
            output.append(
                {
                    "type": "example",
                    "value": example_value,
                }
            )
        example = ""

    def flush_translation_value() -> None:
        """Emit translation text encountered before an inline source annotation."""
        nonlocal translation
        translation_value = clean_text(translation)
        if translation_value:
            output.append({"type": "translation", "value": translation_value})
        translation = ""

    def start_example(operator: str = "") -> None:
        nonlocal definition, example, in_example
        example_prefix = ""
        definition_match = re.match(r"^(.*?)(?:\s*([–~]))\s*$", definition, flags=re.S)
        if definition_match:
            definition = definition_match.group(1)
            operator = operator or definition_match.group(2)
        wrapper_match = re.match(r"^(.*?)([\[(])\s*$", definition, flags=re.S)
        if wrapper_match:
            definition = wrapper_match.group(1)
            example_prefix = wrapper_match.group(2)
        flush_definition()
        example = example_prefix + (f"{operator} " if operator else "")
        in_example = True

    for event in events:
        kind = event["kind"]
        if kind == "label":
            if event["value"] not in labels:
                labels.append(event["value"])
            if in_example:
                flush_example_value()
                flush_translation_value()
            else:
                flush_definition()
            output.append({"type": "label", "value": event["value"]})
            continue
        if kind in {"pronunciation", "expansion"}:
            if in_example:
                flush_example()
            else:
                flush_definition()
            output.append({"type": kind, "value": event["value"]})
            continue
        if kind == "sense":
            if in_example:
                flush_example()
            else:
                flush_definition()
            current_sense = int(event["value"])
            waiting_for_xref = False
            continue
        if kind == "arrow":
            waiting_for_xref = True
            continue
        if kind == "xref":
            if in_example:
                flush_example()
            else:
                flush_definition()
            output.append({"type": "see", "value": clean_text(str(event["value"]))})
            waiting_for_xref = False
            continue
        if kind != "text":
            continue

        value = str(event["value"])
        boundary = str(event.get("boundary", ""))
        if not value:
            continue
        if value.isspace() and in_example and not translation.strip():
            example = join_piece(example, value, boundary)
            continue
        if waiting_for_xref:
            # The actual target is a small-caps event; ignore spacing before it.
            continue
        style = event.get("style", "roman")
        stripped = value.strip()
        if (
            re.fullmatch(r"[.;,:()\[\]]+", stripped)
            and not definition.strip()
            and not example.strip()
            and not translation.strip()
        ):
            continue
        operator_only = bool(re.fullmatch(r"[\s.;,:()\[\]–~]+", value) and re.search(r"[–~]", value))
        punctuation_only = bool(re.fullmatch(r"[\s.,;:!?()\[\]\-]+", value))
        scientific_inline = bool(
            style in {"italic", "bold_italic"}
            and BINOMIAL_RE.fullmatch(stripped)
            and clean_text(definition).endswith(",")
        )
        acronym_inline = bool(
            style in {"italic", "bold_italic"}
            and re.fullmatch(r"[A-Z][A-Z0-9.-]*", stripped)
        )

        if in_example and punctuation_only:
            if translation.strip():
                translation = join_piece(translation, value, boundary)
            else:
                example = join_piece(example, value, boundary)
            continue

        if operator_only:
            operator_match = re.search(r"[–~]", value)
            operator = operator_match.group(0) if operator_match else ""
            if not in_example:
                start_example(operator)
            else:
                example = join_piece(example, operator, boundary or " ")
            continue

        if style in {"italic", "bold_italic"} and not (scientific_inline or acronym_inline):
            if in_example and translation.strip():
                trailing_operator = re.match(
                    r"^(.*?)(?:\s*([–~]))\s*$",
                    translation,
                    flags=re.S,
                )
                next_operator = ""
                if trailing_operator:
                    translation = trailing_operator.group(1)
                    next_operator = trailing_operator.group(2)
                flush_example()
                if next_operator:
                    start_example(next_operator)
            if not in_example:
                start_example()
            example = join_piece(example, value, boundary)
            continue

        if in_example:
            translation = join_piece(translation, value, boundary)
        else:
            definition = join_piece(definition, value, boundary)

    if in_example:
        flush_example()
    else:
        flush_definition()
    return {
        "labels": labels,
        "pronunciation": pronunciation,
        "expansion": expansion,
        "content": output,
    }


def parse_debug_form(
    form: dict[str, Any],
    tag_map: dict[str, dict[str, str]],
    *,
    root_expression: str | None = None,
) -> dict[str, Any]:
    first_line = form["lines"][0]
    zone = leading_expression_zone(first_line)
    following_text = "".join(span["clean_text"] for span in zone["remaining_spans"])
    expression = parse_expression_zone(zone, following_text)
    primary = expression["expression"]
    effective_root = root_expression or primary
    current = expression["inline_subentry"] or primary
    events = content_events(form, zone, expression, tag_map)
    content = parse_form_content(
        events,
        initial_sense=expression["initial_sense"],
        template_operator=expression["template_operator"],
        root_expression=effective_root,
        current_expression=current,
    )
    result = {
        **expression,
        **content,
        "source_line_ids": [line["line_id"] for line in form["lines"]],
    }
    form["expression_parse"] = expression
    return result


def marker_line(marker: str, value: str) -> str:
    return f"[{marker}] {clean_text(value)}".rstrip()


def human_intermediate_text(
    debug_entries: list[dict[str, Any]],
    tag_map: dict[str, dict[str, str]],
) -> str:
    blocks: list[str] = []
    for debug_entry in debug_entries:
        if debug_entry["entry_type"] != "root" or not debug_entry["forms"]:
            continue
        root_form = debug_entry["forms"][0]
        root = parse_debug_form(root_form, tag_map)
        if not root["expression"]:
            continue
        lines = [marker_line("Entry", root["expression"])]
        lines.extend(marker_line("Variant", value) for value in root["variants"])
        if root["homograph"]:
            lines.append(marker_line("Homograph", root["homograph"]))

        active = root
        if root["inline_subentry"]:
            lines.append("")
            lines.append(marker_line("Subentry", root["inline_subentry"]))
        for item in active["content"]:
            marker = item["type"].title()
            if item["type"] == "definition" and item["number"] is not None:
                marker = f"Definition {item['number']}"
            lines.append(marker_line(marker, item["value"]))

        for debug_form in debug_entry["forms"][1:]:
            parsed = parse_debug_form(
                debug_form,
                tag_map,
                root_expression=root["expression"],
            )
            if not parsed["expression"]:
                lines.append(marker_line("Unparsed", debug_form["lines"][0]["clean_text"]))
                continue
            lines.append("")
            lines.append(marker_line("Subentry", parsed["expression"]))
            lines.extend(marker_line("Variant", value) for value in parsed["variants"])
            if parsed["homograph"]:
                lines.append(marker_line("Homograph", parsed["homograph"]))
            if parsed["inline_subentry"]:
                lines.append(marker_line("Note", f"Inline form: {parsed['inline_subentry']}"))
            for item in parsed["content"]:
                marker = item["type"].title()
                if item["type"] == "definition" and item["number"] is not None:
                    marker = f"Definition {item['number']}"
                lines.append(marker_line(marker, item["value"]))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks).rstrip() + "\n"


def write_debug_intermediate(
    path: Path,
    *,
    pdf_path: Path,
    selected_pages: list[int],
    entries: list[dict[str, Any]],
) -> None:
    payload = {
        "schema_version": DEBUG_SCHEMA_VERSION,
        "source_pdf": str(pdf_path.resolve()),
        "source_pdf_sha256": sha256_file(pdf_path),
        "selected_pdf_pages": selected_pages,
        "description": (
            "Agent-facing extraction evidence. Yomitan generation must not consume "
            "this file; human-readable.txt is the lexical generation source."
        ),
        "entries": entries,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_dump(payload, indent=2) + "\n", encoding="utf-8")


def parse_human_intermediate(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current_entry: dict[str, Any] | None = None
    current_form: dict[str, Any] | None = None
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        match = MARKER_RE.fullmatch(raw_line)
        if not match:
            raise ValueError(
                f"{path}:{line_number}: every nonblank line must start with [Marker]"
            )
        marker = match.group(1)
        value = match.group(2) or ""
        if marker == "Entry":
            current_entry = {
                "entry": value,
                "forms": [],
            }
            current_form = {
                "kind": "entry",
                "expression": value,
                "variants": [],
                "homograph": None,
                "labels": [],
                "pronunciation": None,
                "expansion": None,
                "content": [],
            }
            current_entry["forms"].append(current_form)
            entries.append(current_entry)
            continue
        if current_entry is None:
            raise ValueError(f"{path}:{line_number}: [{marker}] appears before [Entry]")
        if marker == "Subentry":
            current_form = {
                "kind": "subentry",
                "expression": value,
                "variants": [],
                "homograph": None,
                "labels": [],
                "pronunciation": None,
                "expansion": None,
                "content": [],
            }
            current_entry["forms"].append(current_form)
            continue
        if current_form is None:
            raise ValueError(f"{path}:{line_number}: [{marker}] has no active form")
        if marker == "Variant":
            current_form["variants"].append(value)
        elif marker == "Homograph":
            current_form["homograph"] = value
        elif marker == "Label":
            current_form["labels"].append(value)
            current_form["content"].append({"type": "label", "value": value})
        elif marker == "Pronunciation":
            current_form["pronunciation"] = value
            current_form["content"].append({"type": "pronunciation", "value": value})
        elif marker == "Expansion":
            current_form["expansion"] = value
            current_form["content"].append({"type": "expansion", "value": value})
        elif marker == "Definition":
            current_form["content"].append(
                {"type": "definition", "number": None, "value": value}
            )
        elif marker.startswith("Definition "):
            number = marker.removeprefix("Definition ")
            if not number.isdigit():
                raise ValueError(f"{path}:{line_number}: invalid definition marker [{marker}]")
            current_form["content"].append(
                {"type": "definition", "number": int(number), "value": value}
            )
        elif marker in {"Example", "Translation", "See", "Note", "Unparsed"}:
            current_form["content"].append({"type": marker.lower(), "value": value})
        else:
            raise ValueError(f"{path}:{line_number}: unknown marker [{marker}]")
    return entries


def expand_optional_form(expression: str, limit: int = 32) -> list[str]:
    variants = [expression]
    while any(OPTIONAL_GROUP_RE.search(value) for value in variants):
        expanded: list[str] = []
        for value in variants:
            match = OPTIONAL_GROUP_RE.search(value)
            if match is None:
                expanded.append(value)
                continue
            optional = match.group(1)
            without = value[: match.start()] + value[match.end() :]
            with_optional = value[: match.start()] + optional + value[match.end() :]
            expanded.extend([without, with_optional])
            if len(expanded) > limit:
                return [expression]
        variants = expanded
    return list(dict.fromkeys(clean_text(value) for value in variants if clean_text(value)))


def lookup_spellings(expression: str) -> list[str]:
    spellings: list[str] = []
    for concrete in expand_optional_form(expression):
        spellings.append(concrete)
        match = AFFIXED_ACRONYM_RE.fullmatch(concrete)
        if match:
            joined = "".join(match.groups())
            spellings.extend([joined, joined.lower()])
    return list(dict.fromkeys(spellings))


def label_badge(code: str, tag_map: dict[str, dict[str, str]]) -> dict[str, Any]:
    row = tag_map.get(code)
    name = row["tag_rename"] if row else code
    color = row["color"] if row else "#626273"
    return {
        "tag": "span",
        "title": f"Source abbreviation: {code}",
        "style": {
            "fontSize": "0.8em",
            "fontWeight": "bold",
            "padding": "0.1em 0.25em",
            "borderRadius": "0.3em",
            "backgroundColor": color,
            "color": "white",
            "wordBreak": "keep-all",
            "marginRight": "0.25em",
        },
        "content": name,
    }


def form_glossary(
    form: dict[str, Any],
    root_expression: str,
    tag_map: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    nodes: list[Any] = []
    if form["homograph"]:
        nodes.append(
            {
                "tag": "div",
                "style": {"fontWeight": "bold"},
                "content": f"Homograph {form['homograph']}",
            }
        )
    for item in form["content"]:
        kind = item["type"]
        if kind == "definition":
            number = item.get("number")
            prefix = f"{number}. " if number is not None else ""
            nodes.append({"tag": "div", "content": [prefix, item["value"]]})
        elif kind == "label":
            nodes.append(
                {
                    "tag": "div",
                    "content": label_badge(item["value"], tag_map),
                }
            )
        elif kind == "pronunciation":
            nodes.append(
                {
                    "tag": "div",
                    "content": f"Pronunciation: {item['value']}",
                }
            )
        elif kind == "expansion":
            nodes.append(
                {
                    "tag": "div",
                    "content": f"Expansion: {item['value']}",
                }
            )
        elif kind == "example":
            nodes.append(
                {
                    "tag": "div",
                    "style": {"fontStyle": "italic", "marginTop": "0.25em"},
                    "content": resolve_example_placeholders(
                        item["value"],
                        root_expression,
                        form["expression"],
                    ),
                }
            )
        elif kind == "translation":
            nodes.append(
                {
                    "tag": "div",
                    "style": {"marginLeft": "1em"},
                    "content": item["value"],
                }
            )
        elif kind == "see":
            target = item["value"]
            nodes.append(
                {
                    "tag": "div",
                    "content": [
                        "See ",
                        {
                            "tag": "a",
                            "href": f"?query={quote(normalize_lookup(target).lower())}",
                            "content": target,
                        },
                    ],
                }
            )
        else:
            nodes.append(
                {
                    "tag": "div",
                    "style": {"color": "#8A4B08"},
                    "content": f"{kind.title()}: {item['value']}",
                }
            )
    if form["kind"] == "subentry":
        nodes.append(
            {
                "tag": "div",
                "style": {"fontSize": "0.8em", "marginTop": "0.35em"},
                "content": [
                    "Root entry: ",
                    {
                        "tag": "a",
                        "href": f"?query={quote(normalize_lookup(root_expression))}",
                        "content": root_expression,
                    },
                ],
            }
        )
    return [{"type": "structured-content", "content": nodes}]


def build_yomitan_from_human(
    human_path: Path,
    output_dir: Path,
    tag_map_path: Path,
    *,
    max_rows_per_bank: int = 10_000,
) -> tuple[Path, int]:
    """Build Yomitan strictly from the on-disk human marker intermediate."""
    human_entries = parse_human_intermediate(human_path)
    tag_map = load_tag_map(tag_map_path)
    yomitan_dir = output_dir / "yomitan"
    yomitan_dir.mkdir(parents=True, exist_ok=True)
    for stale in yomitan_dir.glob("term_bank_*.json"):
        stale.unlink()

    index = {
        "title": "A Comprehensive Indonesian-English Dictionary (Agent 1 pilot)",
        "revision": f"agent1-{SCRIPT_VERSION}",
        "format": 3,
        "sequenced": True,
        "author": "Source by Alan M. Stevens and A. Ed. Schmidgall-Tellings",
        "description": (
            "Pilot generated from the human-readable marker intermediate. "
            "Lexical QA remains in progress."
        ),
        "attribution": "For private extraction testing. Confirm redistribution rights.",
        "sourceLanguage": "id",
        "targetLanguage": "en",
    }
    (yomitan_dir / "index.json").write_text(
        json_dump(index, indent=2) + "\n",
        encoding="utf-8",
    )

    rows: list[list[Any]] = []
    sequence = 0
    for entry in human_entries:
        sequence += 1
        root_expression = entry["entry"]
        for form in entry["forms"]:
            glossary = form_glossary(form, root_expression, tag_map)
            source_spellings = [form["expression"], *form["variants"]]
            emitted: set[tuple[str, str]] = set()
            for source_spelling in source_spellings:
                for spelling in lookup_spellings(source_spelling):
                    folded = normalize_lookup(spelling)
                    reading = spelling if folded != spelling else ""
                    candidates = [(folded, reading)]
                    if folded != spelling:
                        candidates.append((spelling, ""))
                    for expression, candidate_reading in candidates:
                        key = (expression, candidate_reading)
                        if key in emitted:
                            continue
                        emitted.add(key)
                        rows.append(
                            [
                                expression,
                                candidate_reading,
                                "",
                                "",
                                0,
                                glossary,
                                sequence,
                                "",
                            ]
                        )

    bank_paths: list[Path] = []
    for start in range(0, len(rows), max_rows_per_bank):
        bank_number = start // max_rows_per_bank + 1
        bank_path = yomitan_dir / f"term_bank_{bank_number}.json"
        bank_path.write_text(
            json_dump(rows[start : start + max_rows_per_bank]) + "\n",
            encoding="utf-8",
        )
        bank_paths.append(bank_path)

    zip_path = output_dir / "AComprehensive_agent1_pilot.zip"
    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        archive.write(yomitan_dir / "index.json", "index.json")
        for bank_path in bank_paths:
            archive.write(bank_path, bank_path.name)
    return zip_path, len(rows)


def build_pipeline(
    *,
    pdf_path: Path,
    output_dir: Path,
    page_spec: str,
    tag_map_path: Path,
) -> dict[str, Any]:
    if pymupdf is None:
        raise RuntimeError(
            "PyMuPDF is not installed. Install pymupdf==1.28.0 or use --from-human."
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open(pdf_path)
    page_count = document.page_count
    document.close()
    selected_pages = parse_page_spec(page_spec, page_count)
    tag_map = load_tag_map(tag_map_path)

    intermediate_dir = output_dir / "intermediate"
    human_path = intermediate_dir / "human-readable.txt"
    debug_path = intermediate_dir / "debug.json"
    debug_entries = group_debug_entries(pdf_path, selected_pages)
    write_debug_intermediate(
        debug_path,
        pdf_path=pdf_path,
        selected_pages=selected_pages,
        entries=debug_entries,
    )

    # Architectural boundary 1: human output is derived from the on-disk debug
    # intermediate, not directly from live PyMuPDF/parser objects.
    del debug_entries
    debug_payload = json.loads(debug_path.read_text(encoding="utf-8"))
    human_text = human_intermediate_text(debug_payload["entries"], tag_map)
    human_path.write_text(human_text, encoding="utf-8")
    # Keep expression-zone decisions in the agent-facing debug file.
    debug_path.write_text(json_dump(debug_payload, indent=2) + "\n", encoding="utf-8")
    del debug_payload

    # Architectural boundary 2: Yomitan reloads marker text from disk.
    zip_path, row_count = build_yomitan_from_human(
        human_path,
        output_dir,
        tag_map_path,
    )
    parsed_entries = parse_human_intermediate(human_path)
    summary = {
        "schema_version": HUMAN_SCHEMA_VERSION,
        "script_version": SCRIPT_VERSION,
        "data_flow": [
            "PDF",
            "intermediate/debug.json",
            "intermediate/human-readable.txt",
            "Yomitan",
        ],
        "generation_source": "intermediate/human-readable.txt",
        "debug_intermediate": "intermediate/debug.json",
        "selected_pdf_pages": selected_pages,
        "human_entries": len(parsed_entries),
        "human_forms": sum(len(entry["forms"]) for entry in parsed_entries),
        "yomitan_rows": row_count,
        "yomitan_zip": zip_path.name,
    }
    (output_dir / "manifest.json").write_text(
        json_dump(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=script_dir / "acomprehensive.pdf")
    parser.add_argument("--output", type=Path, default=script_dir / "pilot_agent1")
    parser.add_argument("--pages", default=DEFAULT_PAGES)
    parser.add_argument(
        "--tag-map",
        type=Path,
        default=script_dir / "acomprehensive_tags_map.csv",
    )
    parser.add_argument(
        "--from-human",
        type=Path,
        help="Skip PDF extraction and build Yomitan from this marker file.",
    )
    args = parser.parse_args()

    if args.from_human:
        args.output.mkdir(parents=True, exist_ok=True)
        zip_path, row_count = build_yomitan_from_human(
            args.from_human,
            args.output,
            args.tag_map,
        )
        result = {
            "generation_source": str(args.from_human.resolve()),
            "yomitan_rows": row_count,
            "yomitan_zip": str(zip_path.resolve()),
        }
    else:
        result = build_pipeline(
            pdf_path=args.pdf,
            output_dir=args.output,
            page_spec=args.pages,
            tag_map_path=args.tag_map,
        )
    print(json_dump(result, indent=2))


if __name__ == "__main__":
    main()
