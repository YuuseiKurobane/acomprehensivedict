"""Layer 2: convert human-readable marker text into a Yomitan dictionary."""

from __future__ import annotations

import csv
import json
import re
import unicodedata
import zipfile
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import quote


LAYER2_VERSION = "1.0.0"
MARKER_RE = re.compile(r"^\[([^\]]+)\](?:\s(.*))?$")
OPTIONAL_GROUP_RE = re.compile(r"\(([^()]*)\)")
AFFIXED_ACRONYM_RE = re.compile(r"^(.+?)-([A-Z][A-Z0-9]+)-(.+)$")
DEFAULT_MAX_COMPONENT_BYTES = 8_000_000


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
            "Ø": "O", "ø": "o", "Ł": "L", "ł": "l", "Đ": "D", "đ": "d",
            "Ð": "D", "ð": "d", "Þ": "Th", "þ": "th", "Æ": "AE",
            "æ": "ae", "Œ": "OE", "œ": "oe",
        }
    )
    decomposed = unicodedata.normalize("NFKD", text.translate(transliteration))
    folded = "".join(char for char in decomposed if not unicodedata.combining(char))
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
        "pronunciation": None,
        "expansion": None,
        "content": [],
    }


def iter_human_intermediate(path: Path) -> Iterator[dict[str, Any]]:
    """Stream parsed entries so full dictionary builds stay memory-bounded."""
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
                    f"{path}:{line_number}: every nonblank line must start with [Marker]"
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
                raise ValueError(f"{path}:{line_number}: [{marker}] appears before [Entry]")
            if marker == "Subentry":
                current_form = _new_form("subentry", value)
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
                    raise ValueError(
                        f"{path}:{line_number}: invalid definition marker [{marker}]"
                    )
                current_form["content"].append(
                    {"type": "definition", "number": int(number), "value": value}
                )
            elif marker in {"Example", "Translation", "See", "Note", "Unparsed"}:
                current_form["content"].append(
                    {"type": marker.lower(), "value": value}
                )
            else:
                raise ValueError(f"{path}:{line_number}: unknown marker [{marker}]")
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


def resolve_example_placeholders(
    text: str,
    root_expression: str,
    current_expression: str,
) -> str:
    text = re.sub(r"(?<!\w)–(?!\w)", root_expression, text)
    text = text.replace("~", current_expression)
    return clean_text(text)


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
            prefix = f"{item['number']}. " if item.get("number") is not None else ""
            nodes.append({"tag": "div", "content": [prefix, item["value"]]})
        elif kind == "label":
            nodes.append({"tag": "div", "content": label_badge(item["value"], tag_map)})
        elif kind == "pronunciation":
            nodes.append({"tag": "div", "content": f"Pronunciation: {item['value']}"})
        elif kind == "expansion":
            nodes.append({"tag": "div", "content": f"Expansion: {item['value']}"})
        elif kind == "example":
            nodes.append(
                {
                    "tag": "div",
                    "style": {"fontStyle": "italic", "marginTop": "0.25em"},
                    "content": resolve_example_placeholders(
                        item["value"], root_expression, form["expression"]
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


def iter_term_rows(
    human_path: Path,
    tag_map: dict[str, dict[str, str]],
) -> Iterator[list[Any]]:
    sequence = 0
    for entry in iter_human_intermediate(human_path):
        sequence += 1
        root_expression = entry["entry"]
        for form in entry["forms"]:
            glossary = form_glossary(form, root_expression, tag_map)
            emitted: set[tuple[str, str]] = set()
            for source_spelling in [form["expression"], *form["variants"]]:
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
                        yield [expression, reading, "", "", 0, glossary, sequence, ""]


def _write_bank(
    output_dir: Path,
    bank_number: int,
    serialized_rows: list[str],
) -> Path:
    path = output_dir / f"term_bank_{bank_number}.json"
    path.write_text("[" + ",".join(serialized_rows) + "]\n", encoding="utf-8")
    return path


def write_bounded_term_banks(
    rows: Iterable[list[Any]],
    output_dir: Path,
    max_component_bytes: int,
) -> tuple[list[Path], int]:
    """Write compact JSON arrays whose UTF-8 sizes are strictly below the limit."""
    bank_paths: list[Path] = []
    serialized_rows: list[str] = []
    payload_bytes = 3  # [] plus trailing newline
    row_count = 0
    bank_number = 1
    for row in rows:
        serialized = json_dump(row)
        row_bytes = len(serialized.encode("utf-8"))
        if row_bytes + 3 >= max_component_bytes:
            raise ValueError(
                f"One Yomitan row requires {row_bytes} bytes, exceeding the component limit"
            )
        separator_bytes = 1 if serialized_rows else 0
        if serialized_rows and payload_bytes + separator_bytes + row_bytes >= max_component_bytes:
            bank_paths.append(_write_bank(output_dir, bank_number, serialized_rows))
            bank_number += 1
            serialized_rows = []
            payload_bytes = 3
            separator_bytes = 0
        serialized_rows.append(serialized)
        payload_bytes += separator_bytes + row_bytes
        row_count += 1
    if serialized_rows:
        bank_paths.append(_write_bank(output_dir, bank_number, serialized_rows))
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
                f"{path.name} is {size} bytes; it must be under {max_component_bytes}"
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
    output_dir.mkdir(parents=True, exist_ok=True)
    for stale in output_dir.glob("term_bank_*.json"):
        stale.unlink()

    index = {
        "title": "A Comprehensive Indonesian-English Dictionary",
        "revision": f"full-agent1-layer2-{LAYER2_VERSION}",
        "format": 3,
        "sequenced": True,
        "author": "Source by Alan M. Stevens and A. Ed. Schmidgall-Tellings",
        "description": "Generated from the human-readable Layer 1 intermediate.",
        "attribution": "Confirm redistribution rights before publication.",
        "sourceLanguage": "id",
        "targetLanguage": "en",
    }
    index_path = output_dir / "index.json"
    index_path.write_text(json_dump(index, indent=2) + "\n", encoding="utf-8")
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

