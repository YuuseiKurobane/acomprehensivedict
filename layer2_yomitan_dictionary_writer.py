"""Layer 2: render faithful marker text as a compact Yomitan dictionary."""

from __future__ import annotations

import csv
import json
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import parse_qs, quote, urlparse


LAYER2_VERSION = "3.1.1"
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
    text = re.sub(r"\s+([,.;:!?%\]\)])", r"\1", text)
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
            if marker in {"Subentry", "InlineSubentry"}:
                current_form = _new_form(
                    (
                        "inline_subentry"
                        if marker == "InlineSubentry"
                        else "subentry"
                    ),
                    value,
                )
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
                # Compatibility with legacy semantic intermediates. These
                # labels are not generated by the faithful Layer 1.
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


def iter_lookup_candidates(
    source_spellings: Iterable[str],
) -> Iterator[tuple[str, str]]:
    """Yield the expression/reading pairs emitted for source spellings."""
    emitted: set[tuple[str, str]] = set()
    for source_spelling in dict.fromkeys(source_spellings):
        for spelling in lookup_spellings(source_spelling):
            folded = normalize_lookup(spelling)
            candidates = [(folded, spelling if folded != spelling else "")]
            if folded != spelling:
                candidates.append((spelling, ""))
            for candidate in candidates:
                if candidate in emitted:
                    continue
                emitted.add(candidate)
                yield candidate


def _xref_base_text(text: str) -> str:
    """Remove display punctuation and a homograph/sense suffix for lookup."""
    cleaned = clean_text(text)
    cleaned = re.sub(r"\s*[.;:!?]+\s*(?:–\s*)?$", "", cleaned).strip()
    suffix = re.match(
        r"^(.*?)\s+(?:[IVXLCDM]+(?:\s+\d+)?|\d+)\s*$",
        cleaned,
        flags=re.I,
    )
    if suffix is not None and re.search(r"[^\W\d_]", suffix.group(1)):
        cleaned = suffix.group(1).strip()
    return re.sub(r"\s*[.;:!?]+$", "", cleaned).strip()


def _lexical_phrase_candidates(
    form: dict[str, Any],
    root_expression: str,
) -> Iterator[str]:
    """Yield high-confidence source template phrases owned by a form."""
    content = form["content"]
    for index, item in enumerate(content):
        if item["type"] not in {"italic", "bold_italic"}:
            continue
        value = clean_text(str(item.get("value", "")))
        if not value:
            continue
        if value.startswith("~"):
            phrase = clean_text(
                str(form["expression"]) + " " + value.removeprefix("~")
            )
            if phrase:
                yield phrase
        if value.startswith("–"):
            phrase = clean_text(
                root_expression + " " + value.removeprefix("–")
            )
            if phrase:
                yield phrase
        if index:
            previous = content[index - 1]
            if previous["type"] in RUN_KINDS and re.search(
                r"(?<!\w)–\s*$",
                str(previous.get("value", "")),
            ):
                phrase = clean_text(f"{root_expression} {value}")
                if phrase:
                    yield phrase


class CrossReferenceResolver:
    """Resolve source references only when a generated target is defensible."""

    def __init__(self, row_groups: list[dict[str, Any]]) -> None:
        self.lookup_queries: dict[str, set[str]] = defaultdict(set)
        self.query_destinations: dict[str, set[int]] = defaultdict(set)
        self.source_notation_queries: dict[str, set[str]] = defaultdict(set)
        self.embedded_form_queries: dict[str, set[str]] = defaultdict(set)
        self.prefix_queries: dict[str, set[str]] = defaultdict(set)
        self.phrase_queries: dict[str, set[str]] = defaultdict(set)
        self.resolved_methods: Counter[str] = Counter()
        self.unresolved: Counter[str] = Counter()
        self.unresolved_examples: dict[str, list[dict[str, str]]] = defaultdict(
            list
        )

        for group in row_groups:
            source_spellings = [
                spelling
                for form in group["forms"]
                for spelling in [form["expression"], *form["variants"]]
            ]
            candidates = list(iter_lookup_candidates(source_spellings))
            if not candidates:
                continue
            canonical_query = candidates[0][0]
            group["canonical_query"] = canonical_query
            for expression, _reading in candidates:
                self.lookup_queries[expression.casefold()].add(expression)
                self.query_destinations[expression.casefold()].add(
                    int(group["sequence"])
                )

            for source in source_spellings:
                normalized = normalize_lookup(source).casefold()
                self.source_notation_queries[normalized].add(canonical_query)
                words = normalized.split()
                for word_count in range(1, len(words)):
                    prefix = " ".join(words[:word_count])
                    self.prefix_queries[prefix].add(canonical_query)
                if (
                    len(words) == 2
                    and min(len(word) for word in words) >= 3
                    and (
                        words[0] in words[1]
                        or words[1] in words[0]
                    )
                ):
                    for word in words:
                        self.embedded_form_queries[word].add(canonical_query)

            for form in group["forms"]:
                for phrase in _lexical_phrase_candidates(
                    form,
                    str(group["root_expression"]),
                ):
                    self.phrase_queries[
                        normalize_lookup(phrase).casefold()
                    ].add(canonical_query)

    def _unique_query(self, queries: set[str] | None) -> str | None:
        if not queries:
            return None
        casefolded = {query.casefold() for query in queries}
        if len(casefolded) != 1:
            destinations = {
                frozenset(self.query_destinations.get(query, set()))
                for query in casefolded
            }
            if len(destinations) != 1:
                return None
        return sorted(queries, key=lambda value: (len(value), value))[0]

    def resolve_segment(self, text: str) -> dict[str, str] | None:
        base = _xref_base_text(text)
        if not base:
            return None

        normalized = normalize_lookup(base).casefold()
        direct = self._unique_query(self.lookup_queries.get(normalized))
        if direct is not None:
            return {"query": direct, "method": "exact", "target": base}

        source_notation = self._unique_query(
            self.source_notation_queries.get(normalized)
        )
        if source_notation is not None:
            return {
                "query": source_notation,
                "method": "source-notation",
                "target": base,
            }

        optional_queries: set[str] = set()
        for spelling in expand_optional_form(base):
            optional_queries.update(
                self.lookup_queries.get(
                    normalize_lookup(spelling).casefold(),
                    set(),
                )
            )
        optional = self._unique_query(optional_queries)
        if optional is not None:
            return {"query": optional, "method": "optional", "target": base}

        repaired_keys = {
            re.sub(r"(?<=\w)-\s+(?=\w)", "", normalized),
            re.sub(r"(?<=\w)\s+(?=\w)", "", normalized),
            re.sub(r"\s*-\s*", "-", normalized),
            re.sub(r"\s*-\s*", " ", normalized),
            re.sub(r"\s*/\s*", "/", normalized),
        }
        repaired_queries: set[str] = set()
        for key in repaired_keys - {normalized}:
            repaired_queries.update(self.lookup_queries.get(key, set()))
        repaired = self._unique_query(repaired_queries)
        if repaired is not None:
            return {"query": repaired, "method": "spacing", "target": base}

        embedded = self._unique_query(
            self.embedded_form_queries.get(normalized)
        )
        if embedded is not None:
            return {
                "query": embedded,
                "method": "embedded-form",
                "target": base,
            }

        prefix = self._unique_query(self.prefix_queries.get(normalized))
        if prefix is not None:
            return {"query": prefix, "method": "unique-prefix", "target": base}

        phrase = self._unique_query(self.phrase_queries.get(normalized))
        if phrase is not None:
            return {"query": phrase, "method": "source-phrase", "target": base}
        repaired_phrases: set[str] = set()
        for key in repaired_keys - {normalized}:
            repaired_phrases.update(self.phrase_queries.get(key, set()))
        repaired_phrase = self._unique_query(repaired_phrases)
        if repaired_phrase is not None:
            return {
                "query": repaired_phrase,
                "method": "source-phrase-spacing",
                "target": base,
            }
        return None

    def render(
        self,
        value: str,
        *,
        context: tuple[str, str] | None,
    ) -> list[Any]:
        """Render each resolvable target as a link and leave the rest black."""
        nodes: list[Any] = []
        arrow_written = False
        parts = re.split(r"([,;]\s*)", value)
        for part in parts:
            if not part:
                continue
            if re.fullmatch(r"[,;]\s*", part):
                nodes.append(part)
                continue
            visible = f"{'→ ' if not arrow_written else ''}{part}"
            arrow_written = True
            if re.fullmatch(r"\s*[IVXLCDM]+(?:\s+\d+)?\.?\s*", part, re.I):
                nodes.append(visible)
                continue

            resolution = self.resolve_segment(part)
            if resolution is None:
                target = clean_text(part)
                self.unresolved[target] += 1
                if context is not None and len(self.unresolved_examples[target]) < 5:
                    example = {"entry": context[0], "form": context[1]}
                    if example not in self.unresolved_examples[target]:
                        self.unresolved_examples[target].append(example)
                nodes.append(visible)
                continue

            self.resolved_methods[resolution["method"]] += 1
            nodes.append(
                {
                    "tag": "a",
                    "href": (
                        f"?query={quote(resolution['query'])}"
                        "&wildcards=off"
                    ),
                    "content": visible,
                }
            )
        return nodes

    def report(self) -> dict[str, Any]:
        unresolved_rows = [
            {
                "target": target,
                "count": count,
                "examples": self.unresolved_examples.get(target, []),
            }
            for target, count in sorted(
                self.unresolved.items(),
                key=lambda item: (-item[1], item[0].casefold()),
            )
        ]
        return {
            "resolved_target_segments": sum(self.resolved_methods.values()),
            "resolved_by_method": dict(sorted(self.resolved_methods.items())),
            "unresolved_target_segments": sum(self.unresolved.values()),
            "unresolved_unique_targets": len(self.unresolved),
            "unresolved": unresolved_rows,
        }


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
        },
        "content": row["tag_rename"] if row else code,
    }


def _relationship_badge(label: str) -> dict[str, Any]:
    return {
        "tag": "span",
        "style": {
            "fontSize": "0.8em",
            "fontWeight": "bold",
            "padding": "0.1em 0.25em",
            "borderRadius": "0.3em",
            "backgroundColor": "#0000EE",
            "color": "white",
            "wordBreak": "keep-all",
            "marginRight": "0.4em",
        },
        "content": label,
    }


def _relationship_block(
    relationships: dict[str, list[str]],
    resolver: CrossReferenceResolver | None,
) -> dict[str, Any] | None:
    labels = (
        ("Kata Dasar", "parent"),
        ("Kata Turunan", "children"),
        ("Kata Terkait", "related"),
    )
    items: list[dict[str, Any]] = []
    for label, key in labels:
        values = relationships.get(key, [])
        if not values:
            continue
        value_nodes: list[Any] = []
        for index, value in enumerate(values):
            if index:
                value_nodes.append(", ")
            resolution = (
                resolver.resolve_segment(value) if resolver is not None else None
            )
            if resolution is None:
                value_nodes.append(value)
            else:
                value_nodes.append(
                    {
                        "tag": "a",
                        "href": (
                            f"?query={quote(resolution['query'])}"
                            "&wildcards=off"
                        ),
                        "content": value,
                    }
                )
        items.append(
            {
                "tag": "li",
                "content": [_relationship_badge(label), *value_nodes],
            }
        )
    if not items:
        return None
    return {
        "tag": "ul",
        "style": {
            "listStyleType": '"＊"',
            "marginTop": "0.35em",
            "paddingLeft": "1.4em",
        },
        "content": items,
    }


def _strip_label_wrappers(items: list[dict[str, Any]]) -> None:
    """Suppress only wrappers which contain labels and nothing else.

    ``(J coq)`` becomes two badges, while ``(esp one's hand)`` keeps its
    meaningful parentheses. In ``[and ngebubarin (J coq)]`` only the inner
    parentheses are removed; the variant bracket remains visible.
    """
    index = 0
    while index < len(items):
        if items[index]["type"] != "label":
            index += 1
            continue
        group_start = index
        while index + 1 < len(items) and items[index + 1]["type"] == "label":
            index += 1
        group_end = index
        previous = items[group_start - 1] if group_start else None
        following = (
            items[group_end + 1] if group_end + 1 < len(items) else None
        )
        if (
            previous is not None
            and following is not None
            and previous["type"] in RUN_KINDS
            and following["type"] in RUN_KINDS
        ):
            previous_value = str(previous["value"])
            following_value = str(following["value"])
            opening = re.search(r"([\[(])\s*$", previous_value)
            closing = re.match(r"^\s*([\])])", following_value)
            matching = {"(": ")", "[": "]"}
            if (
                opening is not None
                and closing is not None
                and matching[opening.group(1)] == closing.group(1)
            ):
                previous["value"] = previous_value[: opening.start()].rstrip()
                following["value"] = following_value[closing.end() :].lstrip()
        index += 1


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
                separator = "" if root_expression.endswith("-") else " "
                following["value"] = clean_text(
                    f"{root_expression}{separator}{following['value']}"
                )
                continue
        if root_expression.endswith("-"):
            resolved = re.sub(
                r"(?<!\w)–(?!\w)\s*",
                root_expression,
                text,
            )
        else:
            resolved = re.sub(r"(?<!\w)–(?!\w)", root_expression, text)
        item["value"] = clean_text(resolved)
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
    *,
    resolver: CrossReferenceResolver | None = None,
    context: tuple[str, str] | None = None,
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
        if resolver is not None:
            visible = f"→ {value}"
            node = {
                "tag": "span",
                "content": resolver.render(value, context=context),
            }
            return [(node, visible, "see")]
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
    if previous_kind == "sense":
        # The sense span owns its trailing separator space.
        return False
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
    *,
    resolver: "CrossReferenceResolver | None" = None,
    context: tuple[str, str] | None = None,
    initial_surface: str = "",
    initial_kind: str = "",
) -> list[Any]:
    nodes: list[Any] = []
    previous_surface = initial_surface
    previous_kind = initial_kind
    for item in items:
        for node, surface, kind in _item_tokens(
            item,
            tag_map,
            resolver=resolver,
            context=context,
        ):
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
    *,
    resolver: "CrossReferenceResolver | None" = None,
    relationships: dict[str, list[str]] | None = None,
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
            previous_surface = ""
            previous_kind = ""
            if segment_index == 0:
                content.append(_run_node("bold", str(form["expression"])))
                previous_surface = str(form["expression"])
                previous_kind = "bold"
            if segment_index == 0 and form["homograph"]:
                if content:
                    content.append(" ")
                content.append(
                    {
                        "tag": "span",
                        "style": {
                            "fontWeight": "bold",
                            "fontSize": "1.05em",
                        },
                        "content": form["homograph"],
                    }
                )
                previous_surface = str(form["homograph"])
                previous_kind = "bold"
            if segment["number"] is not None:
                if content:
                    content.append(" ")
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
                previous_surface = f"{segment['number']}."
                previous_kind = "sense"
            content.extend(
                inline_content_nodes(
                    segment["items"],
                    tag_map,
                    resolver=resolver,
                    context=(root_expression, str(form["expression"])),
                    initial_surface=previous_surface,
                    initial_kind=previous_kind,
                )
            )

            if (
                form.get("kind") == "inline_subentry"
                and segment_index == 0
                and lines
            ):
                lines[-1]["content"].append(" ")
                lines[-1]["content"].extend(content)
                continue

            line_style: dict[str, str] = {}
            if form_index or segment_index:
                line_style["marginTop"] = (
                    "0.2em" if segment_index == 0 else "0.08em"
                )
            line = {"tag": "div", "content": content}
            if line_style:
                line["style"] = line_style
            lines.append(line)
    if relationships:
        relationship_block = _relationship_block(relationships, resolver)
        if relationship_block is not None:
            lines.append(relationship_block)
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
    if tag in {"span", "div", "ul", "li"}:
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


def _subentry_form_groups(
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    homograph_group_indexes: dict[str, int] = {}
    for entry in entries:
        root_form = entry["forms"][0]
        parent_display = str(entry["entry"])
        if root_form["homograph"]:
            parent_display += f" {root_form['homograph']}"
        for form in entry["forms"][1:]:
            key = unicodedata.normalize("NFC", str(form["expression"]))
            if form["homograph"] and key in homograph_group_indexes:
                group = groups[homograph_group_indexes[key]]
                group["forms"].append(form)
                if parent_display not in group["parent_displays"]:
                    group["parent_displays"].append(parent_display)
            else:
                if form["homograph"]:
                    homograph_group_indexes[key] = len(groups)
                groups.append(
                    {
                        "forms": [form],
                        "parent_displays": [parent_display],
                    }
                )
    return groups


def build_row_groups(human_path: Path) -> list[dict[str, Any]]:
    """Build lookup rows plus the one-level lexical relationship graph."""
    row_groups: list[dict[str, Any]] = []
    sequence = 0
    for entries in iter_entry_groups(human_path):
        root_expression = str(entries[0]["entry"])
        root_forms = [entry["forms"][0] for entry in entries]
        display_forms: list[dict[str, Any]] = []
        for entry in entries:
            root_form = entry["forms"][0]
            display_forms.append(root_form)
            if not root_form["content"]:
                # An empty root followed by derived forms is how the source
                # prints entries such as ``acah I beracah-acah ...``. Include
                # those definitions in the parent display while retaining
                # their independent lookup rows.
                display_forms.extend(entry["forms"][1:])
        subentry_groups = _subentry_form_groups(entries)
        child_names = list(
            dict.fromkeys(
                str(group["forms"][0]["expression"])
                for group in subentry_groups
            )
        )

        sequence += 1
        row_groups.append(
            {
                "kind": "root",
                "forms": root_forms,
                "display_forms": display_forms,
                "root_expression": root_expression,
                "sequence": sequence,
                "relationships": {
                    "parent": [],
                    "children": child_names,
                    "related": [],
                },
            }
        )

        for subentry_group in subentry_groups:
            forms = subentry_group["forms"]
            current = unicodedata.normalize(
                "NFC",
                str(forms[0]["expression"]),
            )
            related = [
                name
                for name in child_names
                if unicodedata.normalize("NFC", name) != current
            ]
            sequence += 1
            row_groups.append(
                {
                    "kind": "subentry",
                    "forms": forms,
                    "root_expression": root_expression,
                    "sequence": sequence,
                    "relationships": {
                        "parent": subentry_group["parent_displays"],
                        "children": [],
                        "related": related,
                    },
                }
            )
    return row_groups


def _form_group_rows(
    forms: list[dict[str, Any]],
    *,
    display_forms: list[dict[str, Any]] | None = None,
    root_expression: str,
    tag_map: dict[str, dict[str, str]],
    sequence: int,
    resolver: CrossReferenceResolver | None = None,
    relationships: dict[str, list[str]] | None = None,
) -> Iterator[list[Any]]:
    glossary = form_glossary(
        display_forms if display_forms is not None else forms,
        root_expression,
        tag_map,
        resolver=resolver,
        relationships=relationships,
    )
    source_spellings: list[str] = []
    for form in forms:
        source_spellings.extend(
            [form["expression"], *form["variants"]]
        )
    for expression, reading in iter_lookup_candidates(source_spellings):
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
    row_groups = build_row_groups(human_path)
    resolver = CrossReferenceResolver(row_groups)
    yield from iter_row_group_term_rows(row_groups, tag_map, resolver)


def iter_row_group_term_rows(
    row_groups: list[dict[str, Any]],
    tag_map: dict[str, dict[str, str]],
    resolver: CrossReferenceResolver,
) -> Iterator[list[Any]]:
    for group in row_groups:
        yield from _form_group_rows(
            group["forms"],
            display_forms=group.get("display_forms"),
            root_expression=str(group["root_expression"]),
            tag_map=tag_map,
            sequence=int(group["sequence"]),
            resolver=resolver,
            relationships=group["relationships"],
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


def _iter_internal_hrefs(node: Any) -> Iterator[str]:
    if isinstance(node, list):
        for child in node:
            yield from _iter_internal_hrefs(child)
        return
    if not isinstance(node, dict):
        return
    if node.get("tag") == "a":
        href = node.get("href")
        if isinstance(href, str) and href.startswith("?"):
            yield href
    if "content" in node:
        yield from _iter_internal_hrefs(node["content"])


def validate_internal_link_targets(bank_paths: Iterable[Path]) -> dict[str, int]:
    """Require every generated internal hyperlink query to name a term."""
    expressions: set[str] = set()
    hrefs: list[str] = []
    for path in bank_paths:
        with path.open("r", encoding="utf-8") as stream:
            rows = json.load(stream)
        for row in rows:
            expressions.add(str(row[0]).casefold())
            hrefs.extend(_iter_internal_hrefs(row[5]))

    unresolved: Counter[str] = Counter()
    for href in hrefs:
        query = parse_qs(urlparse(href).query).get("query", [""])[0]
        if query.casefold() not in expressions:
            unresolved[query] += 1
    if unresolved:
        examples = ", ".join(
            f"{query!r} ({count})"
            for query, count in unresolved.most_common(20)
        )
        raise ValueError(
            "Generated internal hyperlinks have no term resolution: "
            + examples
        )
    return {
        "internal_hyperlinks": len(hrefs),
        "resolved_internal_hyperlinks": len(hrefs),
    }


def _write_reproducible_zip_member(
    archive: zipfile.ZipFile,
    path: Path,
    archive_name: str,
) -> None:
    info = zipfile.ZipInfo(archive_name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (0o100644 & 0xFFFF) << 16
    archive.writestr(
        info,
        path.read_bytes(),
        compress_type=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    )


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
        "revision": "acomprehensive-rc3",
        "format": 3,
        "url": "https://discord.com/invite/9mN2RajgeF",
        "sequenced": True,
        "author": "Alan M. Stevens and A. Ed. Schmidgall-Tellings",
        "description": (
            "Reverse-engineered from the pdf file, available "
            "https://github.com/YuuseiKurobane/acomprehensivedict, "
            "join the Discord server if Github is nuked."
        ),
        "attribution": (
            "Join discord server Belajar Bahasa Indonesia "
            "https://discord.com/invite/9mN2RajgeF"
        ),
        "sourceLanguage": "id",
        "targetLanguage": "en",
    }
    index_path = output_dir / "index.json"
    index_path.write_text(
        json_dump(index, indent=2) + "\n",
        encoding="utf-8",
    )
    tag_map = load_tag_map(tag_map_path)
    row_groups = build_row_groups(human_path)
    resolver = CrossReferenceResolver(row_groups)
    bank_paths, row_count = write_bounded_term_banks(
        iter_row_group_term_rows(row_groups, tag_map, resolver),
        output_dir,
        max_component_bytes,
    )
    components = validate_json_components(
        [index_path, *bank_paths],
        max_component_bytes,
    )
    link_validation = validate_internal_link_targets(bank_paths)
    cross_reference_report = resolver.report()
    cross_reference_report_path = output_dir / "cross_reference_report.json"
    cross_reference_report_path.write_text(
        json_dump(cross_reference_report, indent=2) + "\n",
        encoding="utf-8",
    )

    zip_path = output_dir / f"{dictionary_name}.zip"
    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        _write_reproducible_zip_member(archive, index_path, "index.json")
        for bank_path in bank_paths:
            _write_reproducible_zip_member(
                archive,
                bank_path,
                bank_path.name,
            )
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
        "lexical_row_groups": len(row_groups),
        "term_banks": len(bank_paths),
        "max_component_bytes": max_component_bytes,
        "components": components,
        "zip": str(zip_path.resolve()),
        "zip_crc": "ok",
        "internal_links": link_validation,
        "cross_reference_report": str(
            cross_reference_report_path.resolve()
        ),
        "unresolved_cross_references": cross_reference_report[
            "unresolved_unique_targets"
        ],
    }
