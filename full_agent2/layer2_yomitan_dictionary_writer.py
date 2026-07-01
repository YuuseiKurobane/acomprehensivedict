"""Layer 2: render faithful marker text as a compact Yomitan dictionary."""

from __future__ import annotations

import csv
import json
import re
import unicodedata
import zipfile
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import quote


LAYER2_VERSION = "2.0.1"
MARKER_RE = re.compile(r"^\[([^\]]+)\](?:\s(.*))?$")
OPTIONAL_GROUP_RE = re.compile(r"\(([^()]*)\)")
AFFIXED_ACRONYM_RE = re.compile(r"^(.+?)-([A-Z][A-Z0-9]+)-(.+)$")
ROMAN_SUFFIX_RE = re.compile(r"^(?:[IVXLCDM]+(?:\s+\d+)?|\d+)$")
XREF_SUFFIX_RE = re.compile(
    r"\s+(?:[IVXLCDM]+(?:\s+\d+)?|\d+)\s*$"
)
DEFAULT_MAX_COMPONENT_BYTES = 8_000_000
ALLOWED_STRUCTURED_STYLE_PROPERTIES = {
    "fontStyle",
    "fontWeight",
    "fontSize",
    "color",
    "background",
    "backgroundColor",
    "textDecorationLine",
    "textDecorationStyle",
    "textDecorationColor",
    "borderColor",
    "borderStyle",
    "borderRadius",
    "borderWidth",
    "clipPath",
    "verticalAlign",
    "textAlign",
    "textEmphasis",
    "textShadow",
    "margin",
    "marginTop",
    "marginLeft",
    "marginRight",
    "marginBottom",
    "padding",
    "paddingTop",
    "paddingLeft",
    "paddingRight",
    "paddingBottom",
    "wordBreak",
    "whiteSpace",
    "cursor",
    "listStyleType",
}
RUN_MARKERS = {
    "Roman": "roman",
    "Italic": "italic",
    "Bold": "bold",
    "BoldItalic": "bold_italic",
    "SmallCaps": "small_caps",
    "Symbol": "symbol",
}
RUN_KINDS = set(RUN_MARKERS.values())


def json_dump(value: Any, *, indent: int | None = None) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=indent,
        separators=None if indent else (",", ":"),
    )


def clean_text(text: str) -> str:
    text = text.replace("\t", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.;:!?%\]])", r"\1", text)
    text = re.sub(r"([\[(])\s+", r"\1", text)
    return text


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
    folded = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", folded).strip()


def load_tag_map(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return {row["tag"]: row for row in csv.DictReader(stream)}


def _new_form(kind: str, expression: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "expression": expression,
        "variants": [],
        "homograph": None,
        "labels": [],
        "content": [],
    }


def iter_human_intermediate(path: Path) -> Iterator[dict[str, Any]]:
    """Stream entries from the faithful marker document."""
    current_entry: dict[str, Any] | None = None
    current_form: dict[str, Any] | None = None
    with path.open("r", encoding="utf-8") as stream:
        for line_number, raw_line in enumerate(stream, 1):
            raw_line = raw_line.rstrip("\r\n")
            if not raw_line.strip():
                continue
            match = MARKER_RE.fullmatch(raw_line)
            if not match:
                raise ValueError(
                    f"{path}:{line_number}: every nonblank line must start "
                    "with [Marker]"
                )
            marker = match.group(1)
            value = match.group(2) or ""

            if marker == "Entry":
                if current_entry is not None:
                    yield current_entry
                current_entry = {"entry": value, "forms": []}
                current_form = _new_form("entry", value)
                current_entry["forms"].append(current_form)
                continue
            if current_entry is None:
                raise ValueError(
                    f"{path}:{line_number}: [{marker}] appears before [Entry]"
                )
            if marker == "Subentry":
                current_form = _new_form("subentry", value)
                current_entry["forms"].append(current_form)
                continue
            if current_form is None:
                raise ValueError(
                    f"{path}:{line_number}: [{marker}] has no active form"
                )

            if marker == "Variant":
                current_form["variants"].append(value)
            elif marker == "Homograph":
                current_form["homograph"] = value
            elif marker == "Sense":
                if not value.isdigit():
                    raise ValueError(
                        f"{path}:{line_number}: invalid sense number {value!r}"
                    )
                current_form["content"].append(
                    {"type": "sense", "value": int(value)}
                )
            elif marker == "Label":
                current_form["labels"].append(value)
                current_form["content"].append(
                    {"type": "label", "value": value}
                )
            elif marker == "See":
                current_form["content"].append(
                    {"type": "see", "value": value}
                )
            elif marker in RUN_MARKERS:
                current_form["content"].append(
                    {"type": RUN_MARKERS[marker], "value": value}
                )
            elif marker == "Pronunciation":
                current_form["content"].append(
                    {"type": "pronunciation", "value": value}
                )
            elif marker == "Expansion":
                current_form["content"].append(
                    {"type": "expansion", "value": value}
                )
            elif marker == "Definition":
                # Compatibility with Agent 1 intermediates. These labels are
                # not generated by Agent 2's faithful Layer 1.
                current_form["content"].append(
                    {"type": "roman", "value": value}
                )
            elif marker.startswith("Definition "):
                number = marker.removeprefix("Definition ")
                if not number.isdigit():
                    raise ValueError(
                        f"{path}:{line_number}: invalid marker [{marker}]"
                    )
                current_form["content"].extend(
                    [
                        {"type": "sense", "value": int(number)},
                        {"type": "roman", "value": value},
                    ]
                )
            elif marker == "Example":
                current_form["content"].append(
                    {"type": "italic", "value": value}
                )
            elif marker == "Translation":
                current_form["content"].append(
                    {"type": "roman", "value": value}
                )
            elif marker == "Unparsed":
                current_form["content"].append(
                    {"type": "roman", "value": value}
                )
            elif marker == "Note":
                current_form["content"].append(
                    {"type": "note", "value": value}
                )
            else:
                raise ValueError(
                    f"{path}:{line_number}: unknown marker [{marker}]"
                )
    if current_entry is not None:
        yield current_entry


def parse_human_intermediate(path: Path) -> list[dict[str, Any]]:
    return list(iter_human_intermediate(path))


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
            expanded.extend(
                [
                    value[: match.start()] + value[match.end() :],
                    value[: match.start()] + optional + value[match.end() :],
                ]
            )
            if len(expanded) > limit:
                return [expression]
        variants = expanded
    return list(
        dict.fromkeys(
            clean_text(value)
            for value in variants
            if clean_text(value)
        )
    )


def lookup_spellings(expression: str) -> list[str]:
    spellings: list[str] = []
    for concrete in expand_optional_form(expression):
        spellings.append(concrete)
        match = AFFIXED_ACRONYM_RE.fullmatch(concrete)
        if match:
            joined = "".join(match.groups())
            spellings.extend([joined, joined.lower()])
    return list(dict.fromkeys(spellings))


def split_label_codes(
    source_code: str,
    tag_map: dict[str, dict[str, str]],
) -> list[str]:
    """Apply presentation-only compound-badge splitting.

    A slash is split only when every resulting piece is itself a known source
    tag. Thus Layer 1 can retain J/Jv exactly while Layer 2 displays J and Jv.
    """
    parts = [part.strip() for part in source_code.split("/")]
    if len(parts) > 1 and all(part in tag_map for part in parts):
        return parts
    return [source_code]


def label_badge(code: str, tag_map: dict[str, dict[str, str]]) -> dict[str, Any]:
    row = tag_map.get(code)
    return {
        "tag": "span",
        "title": f"Source abbreviation: {code}",
        "style": {
            "fontSize": "0.8em",
            "fontWeight": "bold",
            "padding": "0.1em 0.25em",
            "borderRadius": "0.3em",
            "backgroundColor": row["color"] if row else "#626273",
            "color": "white",
            "wordBreak": "keep-all",
            "marginRight": "0.25em",
        },
        "content": row["tag_rename"] if row else code,
    }


def _strip_label_wrappers(items: list[dict[str, Any]]) -> None:
    """Suppress source parentheses made redundant by badge presentation."""
    for index, item in enumerate(items):
        if item["type"] != "label":
            continue
        if index:
            previous = items[index - 1]
            if previous["type"] in RUN_KINDS:
                previous["value"] = re.sub(
                    r"\s*[\[(]\s*$",
                    "",
                    str(previous["value"]),
                )
        if index + 1 < len(items):
            following = items[index + 1]
            if following["type"] in RUN_KINDS:
                following["value"] = re.sub(
                    r"^\s*[\])]\s*",
                    "",
                    str(following["value"]),
                )


def _collapse_legacy_xref_suffixes(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Join old adjacent [See] suffix records without inventing new targets."""
    output: list[dict[str, Any]] = []
    index = 0
    while index < len(items):
        item = dict(items[index])
        if item["type"] != "see":
            output.append(item)
            index += 1
            continue
        target = str(item["value"])
        cursor = index + 1
        while (
            cursor < len(items)
            and items[cursor]["type"] == "see"
            and ROMAN_SUFFIX_RE.fullmatch(str(items[cursor]["value"]).strip())
        ):
            target += " " + str(items[cursor]["value"]).strip()
            cursor += 1
        output.append({"type": "see", "value": target})
        index = cursor
    return output


def _resolve_placeholders(
    items: list[dict[str, Any]],
    *,
    root_expression: str,
    current_expression: str,
) -> list[dict[str, Any]]:
    """Resolve source operators after extraction and before DOM rendering."""
    output = [dict(item) for item in items]
    for index, item in enumerate(output):
        if item["type"] not in RUN_KINDS:
            continue
        text = str(item["value"]).replace("~", current_expression)
        trailing_dash = re.search(r"(?<!\w)–\s*$", text)
        if trailing_dash and index + 1 < len(output):
            following = output[index + 1]
            if following["type"] in {"italic", "bold_italic"}:
                item["value"] = clean_text(text[: trailing_dash.start()])
                following["value"] = clean_text(
                    f"{root_expression} {following['value']}"
                )
                continue
        item["value"] = clean_text(
            re.sub(r"(?<!\w)–(?!\w)", root_expression, text)
        )
    return [
        item
        for item in output
        if item["type"] not in RUN_KINDS or str(item["value"]).strip()
    ]


def prepare_content(
    content: list[dict[str, Any]],
    *,
    root_expression: str,
    current_expression: str,
) -> list[dict[str, Any]]:
    items = [dict(item) for item in content]
    _strip_label_wrappers(items)
    items = _collapse_legacy_xref_suffixes(items)
    return _resolve_placeholders(
        items,
        root_expression=root_expression,
        current_expression=current_expression,
    )


def xref_lookup_target(target: str) -> str:
    """Remove a visible Roman/sense suffix from the lookup query only."""
    cleaned = clean_text(target)
    base = XREF_SUFFIX_RE.sub("", cleaned)
    if not base:
        base = cleaned
    return normalize_lookup(base).lower()


def _run_node(kind: str, value: str) -> Any:
    if kind == "roman" or kind == "symbol":
        return value
    style: dict[str, str] = {}
    if kind in {"italic", "bold_italic"}:
        style["fontStyle"] = "italic"
    if kind in {"bold", "bold_italic"}:
        style["fontWeight"] = "bold"
    if kind == "small_caps":
        # Yomitan's v3 structured-content style schema does not permit the
        # CSS fontVariant property. Source small-cap text is already uppercase.
        style["fontWeight"] = "bold"
    return {"tag": "span", "style": style, "content": value}


def _item_tokens(
    item: dict[str, Any],
    tag_map: dict[str, dict[str, str]],
) -> list[tuple[Any, str, str]]:
    kind = str(item["type"])
    value = clean_text(str(item.get("value", "")))
    if not value and kind != "label":
        return []
    if kind in RUN_KINDS:
        return [(_run_node(kind, value), value, kind)]
    if kind == "label":
        return [
            (label_badge(code, tag_map), code, "label")
            for code in split_label_codes(value, tag_map)
        ]
    if kind == "see":
        node = {
            "tag": "a",
            "href": (
                f"?query={quote(xref_lookup_target(value))}"
                "&wildcards=off"
            ),
            "content": f"→ {value}",
        }
        return [(node, f"→ {value}", "see")]
    if kind == "pronunciation":
        return [(_run_node("roman", value), value, kind)]
    if kind == "expansion":
        visible = f"[{value}]"
        return [(_run_node("roman", visible), visible, kind)]
    if kind == "note":
        # Notes in the old intermediate were parser diagnostics, not source
        # dictionary prose. Do not leak them into the reader-facing entry.
        return []
    return [(_run_node("roman", value), value, kind)]


def _needs_space(
    previous_surface: str,
    current_surface: str,
    previous_kind: str,
    current_kind: str,
) -> bool:
    if not previous_surface or not current_surface:
        return False
    if previous_kind == "label":
        return False  # the existing badge margin supplies this spacing
    if current_surface[0] in ".,;:!?%)]}”’":
        return False
    if previous_surface[-1] in "([{“‘/":
        return False
    if current_surface[0] == "/" or previous_surface[-1] == "/":
        return False
    return True


def inline_content_nodes(
    items: list[dict[str, Any]],
    tag_map: dict[str, dict[str, str]],
) -> list[Any]:
    nodes: list[Any] = []
    previous_surface = ""
    previous_kind = ""
    for item in items:
        for node, surface, kind in _item_tokens(item, tag_map):
            if _needs_space(
                previous_surface,
                surface,
                previous_kind,
                kind,
            ):
                nodes.append(" ")
            nodes.append(node)
            previous_surface = surface
            previous_kind = kind
    return nodes


def _sense_segments(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    current = {"number": None, "items": []}
    for item in content:
        if item["type"] != "sense":
            current["items"].append(item)
            continue
        if current["items"] or current["number"] is not None:
            segments.append(current)
        current = {"number": int(item["value"]), "items": []}
    if current["items"] or current["number"] is not None:
        segments.append(current)
    return segments or [{"number": None, "items": []}]


def form_glossary(
    forms: list[dict[str, Any]],
    root_expression: str,
    tag_map: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """Render homographs as Roman sections and senses as compact lines."""
    lines: list[dict[str, Any]] = []
    for form_index, form in enumerate(forms):
        prepared = prepare_content(
            form["content"],
            root_expression=root_expression,
            current_expression=form["expression"],
        )
        segments = _sense_segments(prepared)
        for segment_index, segment in enumerate(segments):
            content: list[Any] = []
            if segment_index == 0 and form["homograph"]:
                content.append(
                    {
                        "tag": "span",
                        "style": {
                            "fontWeight": "bold",
                            "fontSize": "1.05em",
                            "marginRight": "0.45em",
                        },
                        "content": form["homograph"],
                    }
                )
            if segment["number"] is not None:
                content.append(
                    {
                        "tag": "span",
                        "style": {
                            "fontWeight": "bold",
                            "marginRight": "0.35em",
                        },
                        "content": f"{segment['number']}.",
                    }
                )
            content.extend(inline_content_nodes(segment["items"], tag_map))

            line_style: dict[str, str] = {}
            if form_index or segment_index:
                line_style["marginTop"] = (
                    "0.2em" if segment_index == 0 else "0.08em"
                )
            line = {"tag": "div", "content": content}
            if line_style:
                line["style"] = line_style
            lines.append(line)
    return [{"type": "structured-content", "content": lines}]


def validate_structured_content(
    node: Any,
    *,
    path: str = "content",
) -> None:
    """Validate the subset of Yomitan v3 structured content we generate."""
    if isinstance(node, str):
        return
    if isinstance(node, list):
        for index, child in enumerate(node):
            validate_structured_content(child, path=f"{path}/{index}")
        return
    if not isinstance(node, dict):
        raise ValueError(f"{path}: structured content must be text, list, or object")

    tag = node.get("tag")
    if tag in {"span", "div"}:
        allowed_properties = {"tag", "content", "data", "style", "title", "lang"}
    elif tag == "a":
        allowed_properties = {"tag", "content", "href", "lang"}
        href = node.get("href")
        if not isinstance(href, str) or not re.match(r"^(?:https?:|\?)", href):
            raise ValueError(f"{path}: invalid Yomitan link href {href!r}")
    else:
        raise ValueError(f"{path}: unsupported generated tag {tag!r}")

    extra_properties = set(node) - allowed_properties
    if extra_properties:
        raise ValueError(
            f"{path}: unsupported properties for <{tag}>: "
            f"{sorted(extra_properties)}"
        )
    style = node.get("style")
    if style is not None:
        if not isinstance(style, dict):
            raise ValueError(f"{path}/style: expected an object")
        unsupported_styles = set(style) - ALLOWED_STRUCTURED_STYLE_PROPERTIES
        if unsupported_styles:
            raise ValueError(
                f"{path}/style: properties not allowed by Yomitan v3: "
                f"{sorted(unsupported_styles)}"
            )
        if style.get("fontStyle") not in {None, "normal", "italic"}:
            raise ValueError(f"{path}/style/fontStyle: invalid value")
        if style.get("fontWeight") not in {None, "normal", "bold"}:
            raise ValueError(f"{path}/style/fontWeight: invalid value")
        if style.get("wordBreak") not in {
            None,
            "normal",
            "break-all",
            "keep-all",
        }:
            raise ValueError(f"{path}/style/wordBreak: invalid value")
    if "content" in node:
        validate_structured_content(node["content"], path=f"{path}/content")


def validate_term_row(row: list[Any]) -> None:
    if len(row) != 8:
        raise ValueError(f"Yomitan term row must contain 8 fields, got {len(row)}")
    glossary = row[5]
    if not isinstance(glossary, list):
        raise ValueError("Yomitan term row glossary must be an array")
    for index, definition in enumerate(glossary):
        if isinstance(definition, str):
            continue
        if not isinstance(definition, dict):
            raise ValueError(f"glossary/{index}: invalid definition value")
        if set(definition) != {"type", "content"}:
            raise ValueError(
                f"glossary/{index}: invalid structured definition properties"
            )
        if definition["type"] != "structured-content":
            raise ValueError(f"glossary/{index}: unsupported definition type")
        validate_structured_content(
            definition["content"],
            path=f"glossary/{index}/content",
        )


def _entry_group_key(entry: dict[str, Any]) -> str:
    # Group only source-identical expressions. Accent folding is useful for
    # lookup aliases, but would be dishonest as a homograph grouping rule.
    return unicodedata.normalize("NFC", str(entry["entry"]))


def iter_entry_groups(
    human_path: Path,
) -> Iterator[list[dict[str, Any]]]:
    """Group consecutive root homographs while retaining streaming behavior."""
    group: list[dict[str, Any]] = []
    key: str | None = None
    for entry in iter_human_intermediate(human_path):
        entry_key = _entry_group_key(entry)
        entry_has_homograph = bool(entry["forms"][0]["homograph"])
        group_is_homographs = bool(
            group
            and all(item["forms"][0]["homograph"] for item in group)
        )
        if group and (
            entry_key != key
            or not entry_has_homograph
            or not group_is_homographs
        ):
            yield group
            group = []
        group.append(entry)
        key = entry_key
    if group:
        yield group


def _form_group_rows(
    forms: list[dict[str, Any]],
    *,
    root_expression: str,
    tag_map: dict[str, dict[str, str]],
    sequence: int,
) -> Iterator[list[Any]]:
    glossary = form_glossary(forms, root_expression, tag_map)
    source_spellings: list[str] = []
    for form in forms:
        source_spellings.extend(
            [form["expression"], *form["variants"]]
        )
    emitted: set[tuple[str, str]] = set()
    for source_spelling in dict.fromkeys(source_spellings):
        for spelling in lookup_spellings(source_spelling):
            folded = normalize_lookup(spelling)
            candidates = [(folded, spelling if folded != spelling else "")]
            if folded != spelling:
                candidates.append((spelling, ""))
            for expression, reading in candidates:
                key = (expression, reading)
                if key in emitted:
                    continue
                emitted.add(key)
                yield [
                    expression,
                    reading,
                    "",
                    "",
                    0,
                    glossary,
                    sequence,
                    "",
                ]


def iter_term_rows(
    human_path: Path,
    tag_map: dict[str, dict[str, str]],
) -> Iterator[list[Any]]:
    sequence = 0
    for entries in iter_entry_groups(human_path):
        root_expression = str(entries[0]["entry"])
        root_forms = [entry["forms"][0] for entry in entries]
        sequence += 1
        yield from _form_group_rows(
            root_forms,
            root_expression=root_expression,
            tag_map=tag_map,
            sequence=sequence,
        )

        subentry_groups: list[list[dict[str, Any]]] = []
        homograph_group_indexes: dict[str, int] = {}
        for entry in entries:
            for form in entry["forms"][1:]:
                key = unicodedata.normalize("NFC", str(form["expression"]))
                if form["homograph"] and key in homograph_group_indexes:
                    subentry_groups[homograph_group_indexes[key]].append(form)
                else:
                    if form["homograph"]:
                        homograph_group_indexes[key] = len(subentry_groups)
                    subentry_groups.append([form])
        for forms in subentry_groups:
            sequence += 1
            yield from _form_group_rows(
                forms,
                root_expression=root_expression,
                tag_map=tag_map,
                sequence=sequence,
            )


def _write_bank(
    output_dir: Path,
    bank_number: int,
    serialized_rows: list[str],
) -> Path:
    path = output_dir / f"term_bank_{bank_number}.json"
    path.write_text(
        "[" + ",".join(serialized_rows) + "]\n",
        encoding="utf-8",
    )
    return path


def write_bounded_term_banks(
    rows: Iterable[list[Any]],
    output_dir: Path,
    max_component_bytes: int,
) -> tuple[list[Path], int]:
    """Write compact JSON arrays strictly below Yomitan's component limit."""
    bank_paths: list[Path] = []
    serialized_rows: list[str] = []
    payload_bytes = 3
    row_count = 0
    bank_number = 1
    for row in rows:
        validate_term_row(row)
        serialized = json_dump(row)
        row_bytes = len(serialized.encode("utf-8"))
        if row_bytes + 3 >= max_component_bytes:
            raise ValueError(
                f"One Yomitan row requires {row_bytes} bytes, exceeding "
                "the component limit"
            )
        separator_bytes = 1 if serialized_rows else 0
        if (
            serialized_rows
            and payload_bytes + separator_bytes + row_bytes
            >= max_component_bytes
        ):
            bank_paths.append(
                _write_bank(output_dir, bank_number, serialized_rows)
            )
            bank_number += 1
            serialized_rows = []
            payload_bytes = 3
            separator_bytes = 0
        serialized_rows.append(serialized)
        payload_bytes += separator_bytes + row_bytes
        row_count += 1
    if serialized_rows:
        bank_paths.append(
            _write_bank(output_dir, bank_number, serialized_rows)
        )
    return bank_paths, row_count


def validate_json_components(
    paths: Iterable[Path],
    max_component_bytes: int,
) -> list[dict[str, Any]]:
    results = []
    for path in paths:
        size = path.stat().st_size
        if size >= max_component_bytes:
            raise ValueError(
                f"{path.name} is {size} bytes; it must be under "
                f"{max_component_bytes}"
            )
        with path.open("r", encoding="utf-8") as stream:
            json.load(stream)
        results.append({"file": path.name, "bytes": size})
    return results


def build_yomitan(
    *,
    human_path: Path,
    output_dir: Path,
    tag_map_path: Path,
    dictionary_name: str,
    max_component_bytes: int = DEFAULT_MAX_COMPONENT_BYTES,
) -> dict[str, Any]:
    if not human_path.is_file():
        raise FileNotFoundError(human_path)
    if not tag_map_path.is_file():
        raise FileNotFoundError(tag_map_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    for stale in output_dir.glob("term_bank_*.json"):
        stale.unlink()

    index = {
        "title": "A Comprehensive Indonesian-English Dictionary",
        "revision": f"full-agent2-layer2-{LAYER2_VERSION}",
        "format": 3,
        "sequenced": True,
        "author": (
            "Source by Alan M. Stevens and A. Ed. Schmidgall-Tellings"
        ),
        "description": (
            "Generated from faithful typographic Layer 1 marker text; "
            "compact presentation is applied in Layer 2."
        ),
        "attribution": "Confirm redistribution rights before publication.",
        "sourceLanguage": "id",
        "targetLanguage": "en",
    }
    index_path = output_dir / "index.json"
    index_path.write_text(
        json_dump(index, indent=2) + "\n",
        encoding="utf-8",
    )
    tag_map = load_tag_map(tag_map_path)
    bank_paths, row_count = write_bounded_term_banks(
        iter_term_rows(human_path, tag_map),
        output_dir,
        max_component_bytes,
    )
    components = validate_json_components(
        [index_path, *bank_paths],
        max_component_bytes,
    )

    zip_path = output_dir / f"{dictionary_name}.zip"
    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        archive.write(index_path, "index.json")
        for bank_path in bank_paths:
            archive.write(bank_path, bank_path.name)
    with zipfile.ZipFile(zip_path) as archive:
        bad_member = archive.testzip()
    if bad_member is not None:
        raise ValueError(f"ZIP CRC failed for {bad_member}")

    return {
        "layer": 2,
        "layer2_version": LAYER2_VERSION,
        "generation_source": str(human_path.resolve()),
        "dictionary_name": dictionary_name,
        "yomitan_rows": row_count,
        "term_banks": len(bank_paths),
        "max_component_bytes": max_component_bytes,
        "components": components,
        "zip": str(zip_path.resolve()),
        "zip_crc": "ok",
    }
