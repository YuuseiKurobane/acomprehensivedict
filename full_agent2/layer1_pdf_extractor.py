"""Layer 1: faithfully extract structure and typographic runs from the PDF.

Layer 1 deliberately does not guess Definition/Example/Translation roles.
It retains only constrained lexical structure (entries, homographs, senses,
labels, and arrow-led cross-references) plus the typography actually observed
in the source PDF. Presentation decisions belong to Layer 2.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


WORK_DIR = Path(__file__).resolve().parent
REPO_DIR = WORK_DIR.parent
GROUND_TRUTH_PATH = REPO_DIR / "extract_agent1.py"
EXPECTED_GROUND_TRUTH_SHA256 = (
    "fa37367c55b7d5e5c57b99b77b4a331933fcf1b59e929fcb82545a2cbb18634f"
)
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

import extract_agent1 as source_parser  # noqa: E402


LAYER1_VERSION = "2.0.0"
SMALL_PAGE_SPEC = source_parser.DEFAULT_PAGES
FULL_PAGE_SPEC = "21-1123"
PROFILE_PAGE_SPECS = {
    "small": SMALL_PAGE_SPEC,
    "full": FULL_PAGE_SPEC,
}
RUN_STYLE_MARKERS = {
    "roman": "Roman",
    "italic": "Italic",
    "bold": "Bold",
    "bold_italic": "BoldItalic",
    "small_caps": "SmallCaps",
    "symbol": "Symbol",
}


def normalized_source_sha256(path: Path) -> str:
    """Hash Python source consistently across LF and CRLF checkouts."""
    source = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(source).hexdigest()


def profile_paths(intermediate_dir: Path, profile: str) -> dict[str, Path]:
    if profile not in PROFILE_PAGE_SPECS:
        raise ValueError(f"Unknown Layer 1 profile: {profile}")
    return {
        "human": intermediate_dir / f"human_readable_{profile}.txt",
        "debug": intermediate_dir / f"debug_{profile}.json",
        "manifest": intermediate_dir / f"layer1_{profile}_manifest.json",
    }


def _span_events(
    span: dict[str, Any],
    tag_map: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """Translate one observed PDF span without assigning speculative roles."""
    text = str(span["clean_text"])
    stripped = text.strip()
    style = str(span["style"])
    if not stripped:
        return (
            [{"kind": "run", "style": style, "value": text, "boundary": ""}]
            if text
            else []
        )

    if style in {"italic", "bold_italic"}:
        codes = source_parser.tag_codes(stripped, tag_map)
        if codes:
            return [
                {"kind": "label", "value": code, "boundary": ""}
                for code in codes
            ]

    if style in {"bold", "bold_italic"} and source_parser.SENSE_RE.fullmatch(text):
        return [{"kind": "sense", "value": int(stripped), "boundary": ""}]
    if style == "symbol" and stripped == "→":
        return [{"kind": "arrow", "value": "→", "boundary": ""}]
    return [
        {
            "kind": "run",
            "style": style,
            "value": text,
            "boundary": "",
        }
    ]


def _raw_content_events(
    form: dict[str, Any],
    zone: dict[str, Any],
    parsed_zone: dict[str, Any],
    tag_map: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    lines = form["lines"]
    for line_index, line in enumerate(lines):
        spans = zone["remaining_spans"] if line_index == 0 else line["spans"]
        first_event_on_line = True
        line_boundary = ""
        if line_index:
            line_boundary = (
                "" if lines[line_index - 1]["join_next_without_space"] else " "
            )
        for span in spans:
            for event in _span_events(span, tag_map):
                if first_event_on_line:
                    event["boundary"] = line_boundary
                    first_event_on_line = False
                events.append(event)

    pronunciation_prefix = parsed_zone["pronunciation_prefix"]
    if pronunciation_prefix:
        events.insert(
            0,
            {
                "kind": "run",
                "style": "roman",
                "value": pronunciation_prefix,
                "boundary": "",
            },
        )
    if parsed_zone["template_operator"]:
        # The operator was physically part of the bold headword zone.
        events.insert(
            0,
            {
                "kind": "run",
                "style": "bold",
                "value": parsed_zone["template_operator"],
                "boundary": "",
            },
        )
    if parsed_zone["initial_sense"] is not None:
        events.insert(
            0,
            {
                "kind": "sense",
                "value": int(parsed_zone["initial_sense"]),
                "boundary": "",
            },
        )
    return events


def _arrow_cross_references(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pair an arrow only with immediately following small-cap source runs."""
    output: list[dict[str, Any]] = []
    index = 0
    while index < len(events):
        event = events[index]
        if event["kind"] != "arrow":
            output.append(event)
            index += 1
            continue

        cursor = index + 1
        while (
            cursor < len(events)
            and events[cursor]["kind"] == "run"
            and not str(events[cursor]["value"]).strip()
        ):
            cursor += 1

        target_parts: list[str] = []
        last_target_end = cursor
        while (
            cursor < len(events)
            and events[cursor]["kind"] == "run"
            and events[cursor]["style"] == "small_caps"
        ):
            value = source_parser.clean_text(str(events[cursor]["value"]))
            if value:
                target_parts.append(value)
            last_target_end = cursor + 1
            lookahead = last_target_end
            while (
                lookahead < len(events)
                and events[lookahead]["kind"] == "run"
                and not str(events[lookahead]["value"]).strip()
            ):
                lookahead += 1
            if (
                lookahead < len(events)
                and events[lookahead]["kind"] == "run"
                and events[lookahead]["style"] == "small_caps"
            ):
                cursor = lookahead
            else:
                break

        if target_parts:
            output.append(
                {
                    "kind": "see",
                    "value": " ".join(target_parts),
                    "boundary": str(event.get("boundary", "")),
                }
            )
            index = last_target_end
        else:
            output.append(
                {
                    "kind": "run",
                    "style": "symbol",
                    "value": "→",
                    "boundary": str(event.get("boundary", "")),
                }
            )
            index += 1
    return output


def _coalesce_runs(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join physically contiguous runs while retaining style boundaries."""
    output: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal active
        if active is not None:
            value = source_parser.clean_text(str(active["value"]))
            if value:
                output.append({**active, "value": value})
        active = None

    for event in events:
        if event["kind"] != "run":
            flush()
            output.append(event)
            continue
        if active is None or active["style"] != event["style"]:
            flush()
            active = dict(event)
            continue
        active["value"] = source_parser.join_piece(
            str(active["value"]),
            str(event["value"]),
            str(event.get("boundary", "")),
        )
    flush()
    return output


def parse_debug_form(
    form: dict[str, Any],
    tag_map: dict[str, dict[str, str]],
    *,
    root_expression: str | None = None,
) -> dict[str, Any]:
    """Parse high-confidence form structure and retain honest body runs."""
    first_line = form["lines"][0]
    zone = source_parser.leading_expression_zone(first_line)
    following_text = "".join(
        span["clean_text"] for span in zone["remaining_spans"]
    )
    expression = source_parser.parse_expression_zone(zone, following_text)
    raw_events = _raw_content_events(form, zone, expression, tag_map)
    content = _coalesce_runs(_arrow_cross_references(raw_events))
    form["expression_parse"] = expression
    return {
        **expression,
        "root_expression": root_expression or expression["expression"],
        "content": content,
        "source_line_ids": [line["line_id"] for line in form["lines"]],
    }


def marker_line(marker: str, value: Any) -> str:
    cleaned = source_parser.clean_text(str(value))
    return f"[{marker}] {cleaned}".rstrip()


def _content_marker_lines(content: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in content:
        kind = item["kind"]
        if kind == "run":
            marker = RUN_STYLE_MARKERS.get(str(item["style"]))
            if marker is None:
                raise ValueError(f"Unknown observed run style: {item['style']}")
            lines.append(marker_line(marker, item["value"]))
        elif kind == "sense":
            lines.append(marker_line("Sense", item["value"]))
        elif kind == "label":
            lines.append(marker_line("Label", item["value"]))
        elif kind == "see":
            lines.append(marker_line("See", item["value"]))
        else:
            raise ValueError(f"Unknown faithful content event: {kind}")
    return lines


def human_intermediate_text(
    debug_entries: list[dict[str, Any]],
    tag_map: dict[str, dict[str, str]],
) -> str:
    """Create the typography-preserving Layer 1 marker document."""
    blocks: list[str] = []
    for debug_entry in debug_entries:
        if debug_entry["entry_type"] != "root" or not debug_entry["forms"]:
            continue
        root = parse_debug_form(debug_entry["forms"][0], tag_map)
        if not root["expression"]:
            continue

        lines = [marker_line("Entry", root["expression"])]
        lines.extend(marker_line("Variant", value) for value in root["variants"])
        if root["homograph"]:
            lines.append(marker_line("Homograph", root["homograph"]))
        if root["inline_subentry"]:
            lines.extend(["", marker_line("Subentry", root["inline_subentry"])])
        lines.extend(_content_marker_lines(root["content"]))

        for debug_form in debug_entry["forms"][1:]:
            parsed = parse_debug_form(
                debug_form,
                tag_map,
                root_expression=root["expression"],
            )
            if not parsed["expression"]:
                lines.append(
                    marker_line("Unparsed", debug_form["lines"][0]["clean_text"])
                )
                continue
            lines.extend(["", marker_line("Subentry", parsed["expression"])])
            lines.extend(
                marker_line("Variant", value) for value in parsed["variants"]
            )
            if parsed["homograph"]:
                lines.append(marker_line("Homograph", parsed["homograph"]))
            if parsed["inline_subentry"]:
                lines.append(
                    marker_line("Note", f"Inline form: {parsed['inline_subentry']}")
                )
            lines.extend(_content_marker_lines(parsed["content"]))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks).rstrip() + "\n"


def run_layer1(
    *,
    pdf_path: Path,
    intermediate_dir: Path,
    tag_map_path: Path,
    profile: str,
    page_spec: str | None = None,
) -> dict[str, Any]:
    """Run PDF extraction and write the named Layer 1 profile outputs."""
    if source_parser.pymupdf is None:
        raise RuntimeError("PyMuPDF is required for Layer 1 extraction.")
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    if not tag_map_path.is_file():
        raise FileNotFoundError(tag_map_path)
    if profile not in PROFILE_PAGE_SPECS:
        raise ValueError(f"Unknown Layer 1 profile: {profile}")

    parser_sha256 = normalized_source_sha256(GROUND_TRUTH_PATH)
    if parser_sha256 != EXPECTED_GROUND_TRUTH_SHA256:
        raise RuntimeError(
            "extract_agent1.py changed after the low-level grammar was pinned. "
            f"Expected {EXPECTED_GROUND_TRUTH_SHA256}, got {parser_sha256}."
        )

    effective_page_spec = page_spec or PROFILE_PAGE_SPECS[profile]
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    paths = profile_paths(intermediate_dir, profile)

    document = source_parser.pymupdf.open(pdf_path)
    page_count = document.page_count
    document.close()
    selected_pages = source_parser.parse_page_spec(
        effective_page_spec,
        page_count,
    )
    tag_map = source_parser.load_tag_map(tag_map_path)
    debug_entries = source_parser.group_debug_entries(pdf_path, selected_pages)
    human_text = human_intermediate_text(debug_entries, tag_map)
    paths["human"].write_text(human_text, encoding="utf-8")
    source_parser.write_debug_intermediate(
        paths["debug"],
        pdf_path=pdf_path,
        selected_pages=selected_pages,
        entries=debug_entries,
    )

    summary = {
        "layer": 1,
        "layer1_version": LAYER1_VERSION,
        "schema": "faithful-typographic-runs-1",
        "parser_ground_truth": str(GROUND_TRUTH_PATH.resolve()),
        "parser_ground_truth_sha256": parser_sha256,
        "parser_version": source_parser.SCRIPT_VERSION,
        "profile": profile,
        "requested_page_spec": effective_page_spec,
        "selected_pdf_pages": (
            selected_pages
            if len(selected_pages) <= 100
            else {
                "first": selected_pages[0],
                "last": selected_pages[-1],
                "count": len(selected_pages),
            }
        ),
        "pdf_page_count": page_count,
        "pdf_sha256": source_parser.sha256_file(pdf_path),
        "human_entries": sum(
            line.startswith("[Entry] ") for line in human_text.splitlines()
        ),
        "human_subentries": sum(
            line.startswith("[Subentry] ") for line in human_text.splitlines()
        ),
        "numbered_senses": sum(
            line.startswith("[Sense] ") for line in human_text.splitlines()
        ),
        "human_output": str(paths["human"].resolve()),
        "debug_output": str(paths["debug"].resolve()),
    }
    paths["manifest"].write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary
