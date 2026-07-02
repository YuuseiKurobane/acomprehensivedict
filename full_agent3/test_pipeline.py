from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

import layer1_pdf_extractor as layer1
import layer2_yomitan_dictionary_writer as layer2


def span(style: str, text: str) -> dict[str, str]:
    return {"style": style, "clean_text": text}


def flatten(node: Any) -> str:
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(flatten(child) for child in node)
    if isinstance(node, dict):
        return flatten(node.get("content", ""))
    return ""


def anchors(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, list):
        for child in node:
            found.extend(anchors(child))
    elif isinstance(node, dict):
        if node.get("tag") == "a":
            found.append(node)
        found.extend(anchors(node.get("content", [])))
    return found


class Layer1SurfaceTests(unittest.TestCase):
    def test_variant_surface_is_not_consumed(self) -> None:
        form = {
            "lines": [
                {
                    "line_id": "test",
                    "join_next_without_space": False,
                    "spans": [
                        span("bold", "membubarkan"),
                        span("roman", " [and "),
                        span("bold", "ngebubarin "),
                        span("roman", "("),
                        span("italic", "J coq"),
                        span("roman", ")] "),
                        span("bold", "1"),
                        span("roman", " to disperse."),
                    ],
                }
            ]
        }
        parsed = layer1.parse_debug_form(
            form,
            {"J": {}, "coq": {}},
            root_expression="bubar",
        )
        self.assertEqual(parsed["expression"], "membubarkan")
        self.assertEqual(parsed["variants"], ["ngebubarin"])
        self.assertEqual(
            [(item["kind"], item.get("value")) for item in parsed["content"]],
            [
                ("run", "[and"),
                ("run", "ngebubarin"),
                ("run", "("),
                ("label", "J"),
                ("label", "coq"),
                ("run", ")]"),
                ("sense", 1),
                ("run", "to disperse."),
            ],
        )

    def test_labels_do_not_hide_inline_aliases(self) -> None:
        form = {
            "lines": [
                {
                    "line_id": "test",
                    "join_next_without_space": False,
                    "spans": [
                        span("bold", "aco"),
                        span("roman", " ("),
                        span("italic", "Jv"),
                        span("roman", ") "),
                        span("bold", "mengaco"),
                        span("roman", " [and "),
                        span("bold", "ngaco"),
                        span("roman", " ("),
                        span("italic", "coq"),
                        span("roman", ")] "),
                        span("bold", "1"),
                        span("roman", " definition."),
                    ],
                }
            ]
        }
        parsed = layer1.parse_debug_form(
            form,
            {"Jv": {}, "coq": {}},
        )
        self.assertEqual(parsed["variants"], ["mengaco", "ngaco"])
        self.assertEqual(parsed["content"][0]["value"], "(")
        self.assertEqual(parsed["content"][2]["value"], ")")


class Layer2PresentationTests(unittest.TestCase):
    MARKERS = """\
[Entry] acau mengacau
[Roman] definition.

[Entry] aco
[Variant] mengaco
[Variant] ngaco
[Roman] (
[Label] Jv
[Roman] )
[Bold] mengaco
[Roman] [and
[Bold] ngaco
[Roman] (
[Label] coq
[Roman] )]
[Sense] 1
[Roman] definition;
[See] ACAU
[Roman] .

[Entry] acung
[Homograph] I
[Roman] point.

[Subentry] mengacung
[Roman] to point.

[Subentry] mengacungi
[Roman] to point out.

[Entry] adad
[Roman] a fish.

[Entry] deadref
[See] DOES NOT EXIST
"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "markers.txt"
        self.path.write_text(self.MARKERS, encoding="utf-8")
        self.tag_map = {
            "Jv": {"tag_rename": "Javanese", "color": "#626273"},
            "coq": {"tag_rename": "Colloquial", "color": "#F25AA6"},
        }
        self.groups = layer2.build_row_groups(self.path)
        self.resolver = layer2.CrossReferenceResolver(self.groups)
        self.rows = list(
            layer2.iter_row_group_term_rows(
                self.groups,
                self.tag_map,
                self.resolver,
            )
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def rows_for(self, expression: str) -> list[list[Any]]:
        return [
            row
            for row in self.rows
            if str(row[0]).casefold() == expression.casefold()
        ]

    def test_aliases_share_the_complete_source_line(self) -> None:
        rows = [
            self.rows_for(expression)[0]
            for expression in ("aco", "mengaco", "ngaco")
        ]
        self.assertEqual({row[6] for row in rows}, {2})
        for row in rows:
            text = flatten(row[5][0]["content"][0])
            self.assertTrue(text.startswith("aco Javanesemengaco [and ngaco"))
            self.assertTrue(text.endswith("Colloquial]"))
        hrefs = anchors(rows[0][5])
        self.assertIn(
            "?query=acau%20mengacau&wildcards=off",
            {item["href"] for item in hrefs},
        )

    def test_every_entry_names_itself(self) -> None:
        row = self.rows_for("adad")[0]
        first_line = flatten(row[5][0]["content"][0])
        self.assertEqual(first_line, "adad a fish.")

    def test_child_names_itself_and_links_relations_at_bottom(self) -> None:
        row = self.rows_for("mengacung")[0]
        content = row[5][0]["content"]
        self.assertEqual(flatten(content[0]), "mengacung to point.")
        bottom = content[-1]
        self.assertIn("Kata Dasaracung", flatten(bottom))
        self.assertIn("Kata Terkaitmengacungi", flatten(bottom))
        hrefs = {item["href"] for item in anchors(bottom)}
        self.assertIn("?query=acung&wildcards=off", hrefs)
        self.assertIn("?query=mengacungi&wildcards=off", hrefs)

    def test_unresolved_reference_is_black_text_not_an_anchor(self) -> None:
        row = self.rows_for("deadref")[0]
        content = row[5][0]["content"]
        self.assertIn("→ DOES NOT EXIST", flatten(content))
        self.assertFalse(
            any(
                "DOES NOT EXIST" in flatten(item)
                for item in anchors(content)
            )
        )

    def test_label_wrapper_removal_is_balanced(self) -> None:
        qualified = [
            {"type": "roman", "value": "("},
            {"type": "label", "value": "esp"},
            {"type": "roman", "value": "one's hand)"},
        ]
        prepared = layer2.prepare_content(
            qualified,
            root_expression="acung",
            current_expression="mengacungkan",
        )
        self.assertEqual(prepared[0]["value"], "(")
        self.assertEqual(prepared[2]["value"], "one's hand)")

        label_only = [
            {"type": "roman", "value": "("},
            {"type": "label", "value": "J"},
            {"type": "label", "value": "coq"},
            {"type": "roman", "value": ")]"},
        ]
        prepared = layer2.prepare_content(
            label_only,
            root_expression="bubar",
            current_expression="membubarkan",
        )
        self.assertEqual(
            [item["type"] for item in prepared],
            ["label", "label", "roman"],
        )
        self.assertEqual(prepared[-1]["value"], "]")


if __name__ == "__main__":
    unittest.main()
