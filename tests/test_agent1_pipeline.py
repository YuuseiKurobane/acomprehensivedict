from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import extract_agent1 as pipeline


ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "acomprehensive.pdf"
TAGS = ROOT / "acomprehensive_tags_map.csv"


class ExtractionRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        tag_map = pipeline.load_tag_map(TAGS)
        debug = pipeline.group_debug_entries(PDF, [21, 23, 25, 70, 236, 500])
        cls.human_text = pipeline.human_intermediate_text(debug, tag_map)
        with tempfile.TemporaryDirectory() as temp_dir:
            cls.human_path = Path(temp_dir) / "human-readable.txt"
            cls.human_path.write_text(cls.human_text, encoding="utf-8")
            cls.entries = pipeline.parse_human_intermediate(cls.human_path)

    def find_form(
        self,
        expression: str,
        *,
        homograph: str | None = None,
        kind: str | None = None,
    ) -> dict:
        for entry in self.entries:
            for form in entry["forms"]:
                if form["expression"] != expression:
                    continue
                if homograph is not None and form["homograph"] != homograph:
                    continue
                if kind is not None and form["kind"] != kind:
                    continue
                return form
        self.fail(f"Form not found: {expression!r}, homograph={homograph!r}")

    def content_values(self, form: dict, kind: str) -> list[str]:
        return [item["value"] for item in form["content"] if item["type"] == kind]

    def test_multispan_expression_variants(self) -> None:
        first_a = self.find_form("a", homograph="I")
        self.assertIn("A", first_a["variants"])
        self.assertEqual("/a/", first_a["pronunciation"])

        aala = self.find_form("aala")
        self.assertIn("a’ala", aala["variants"])

        accented_a = self.find_form("à")
        self.assertIn("@", accented_a["variants"])
        self.assertIn("– Rp 1.000,-", self.content_values(accented_a, "example"))

        year_form = self.find_form("A ’45")
        self.assertEqual("Angkatan ’45", year_form["expansion"])

        mengendalikan = self.find_form("mengendalikan", kind="subentry")
        self.assertIn("ngendaliin", mengendalikan["variants"])
        terkendali = self.find_form("terkendali(kan)", kind="subentry")
        self.assertIn("kekendali", terkendali["variants"])

    def test_structural_markers_do_not_pollute_expressions(self) -> None:
        aci = self.find_form("aci", homograph="IV")
        definitions = [
            item for item in aci["content"] if item["type"] == "definition"
        ]
        self.assertEqual(1, definitions[0]["number"])
        self.assertEqual(2, definitions[1]["number"])

        abang = self.find_form("abang", homograph="III")
        self.assertNotIn("–", abang["expression"])

        berkendali = self.find_form("berkendali", kind="subentry")
        self.assertEqual(
            [1, 2],
            [
                item["number"]
                for item in berkendali["content"]
                if item["type"] == "definition"
            ],
        )

    def test_inline_subentry_and_template(self) -> None:
        anyang = self.find_form("anyang", homograph="II")
        root_entry = next(entry for entry in self.entries if anyang in entry["forms"])
        menganyang = next(
            form for form in root_entry["forms"] if form["expression"] == "menganyang"
        )
        self.assertIn(
            "~ hati",
            self.content_values(menganyang, "example"),
        )

    def test_soft_hyphen_reflow(self) -> None:
        a_major = self.find_form("A", homograph="V")
        self.assertIn("C major.", self.content_values(a_major, "definition")[0])

        cupet = self.find_form("cupet")
        combined = " ".join(item["value"] for item in cupet["content"])
        self.assertIn("percent", combined)
        self.assertIn("bigoted", combined)
        self.assertNotIn("per cent", combined)
        self.assertNotIn("big oted", combined)

        aam = self.find_form("aam")
        self.assertIn(
            "General Chairman (of the NU);",
            self.content_values(aam, "translation"),
        )

    def test_cupet_marker_shape(self) -> None:
        cupet = self.find_form("cupet")
        self.assertEqual(["J"], cupet["labels"])
        definitions = [
            item for item in cupet["content"] if item["type"] == "definition"
        ]
        self.assertEqual([1, 2, 3], [item["number"] for item in definitions])
        self.assertIn(
            "Soalnya uang belanja dapur terasa menjadi – (sempit)",
            self.content_values(cupet, "example")[0],
        )
        self.assertIn("PICIK", self.content_values(cupet, "see"))

        entry = next(entry for entry in self.entries if cupet in entry["forms"])
        kecupetan = next(
            form for form in entry["forms"] if form["expression"] == "kecupetan"
        )
        self.assertEqual(
            [1, 2],
            [
                item["number"]
                for item in kecupetan["content"]
                if item["type"] == "definition"
            ],
        )

        cuping = self.find_form("cuping")
        self.assertIn(
            "tidak memperlihatkan – hidungnya",
            self.content_values(cuping, "example"),
        )

    def test_substitution_dash_binds_to_following_example(self) -> None:
        abu = self.find_form("abu", homograph="I")
        self.assertIn("– bara", self.content_values(abu, "example"))
        self.assertIn("– batu bara", self.content_values(abu, "example"))
        definitions = [
            item["value"]
            for item in abu["content"]
            if item["type"] == "definition"
        ]
        self.assertEqual("dust.", definitions[1])
        self.assertIn(
            "(seperti) – di atas tunggul",
            self.content_values(abu, "example"),
        )
        self.assertIn(
            "to stir up a hornet’s nest.",
            self.content_values(abu, "translation"),
        )
        self.assertNotIn(
            "to stir up a hornet’s nest. –",
            self.content_values(abu, "translation"),
        )
        self.assertFalse(
            any(value.endswith("–") for value in self.content_values(abu, "translation"))
        )

        abuk = self.find_form("abuk", homograph="I")
        self.assertEqual(
            ["– bunga", "– gergaji"],
            self.content_values(abuk, "example"),
        )
        self.assertEqual(
            ["pollen.", "sawdust."],
            self.content_values(abuk, "translation"),
        )

    def test_labels_preserve_source_position(self) -> None:
        abu = self.find_form("abu", homograph="I")
        content = [(item["type"], item["value"]) for item in abu["content"]]
        self.assertLess(
            content.index(("example", "berdiang di – dingin")),
            content.index(("label", "M"), content.index(("example", "berdiang di – dingin"))),
        )
        second_m = content.index(
            ("label", "M"),
            content.index(("example", "berdiang di – dingin")),
        )
        self.assertLess(
            second_m,
            content.index(
                ("translation", "to obtain nothing (from brothers/family heads, etc.).")
            ),
        )
        javanese = content.index(("label", "Jv"))
        self.assertLess(content.index(("example", "– blarak")), javanese)
        self.assertLess(
            javanese,
            content.index(
                ("translation", "dried coconut leaf powder (used as a cleanser).")
            ),
        )

        abubakar = self.find_form("abubakar", homograph="II")
        metadata = [
            (item["type"], item["value"])
            for item in abubakar["content"]
            if item["type"] in {"label", "expansion"}
        ]
        self.assertEqual(
            [
                ("label", "joc"),
                ("label", "acr"),
                ("expansion", "atas budi baik Golkar"),
            ],
            metadata,
        )


class HumanBoundaryTests(unittest.TestCase):
    def test_yomitan_builder_reads_marker_file(self) -> None:
        source = (
            "[Entry] tést\n"
            "[Variant] ujian\n"
            "[Label] J\n"
            "[Definition] from the marker intermediate.\n"
            "[See] COBA\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            human = temp / "human-readable.txt"
            output = temp / "output"
            human.write_text(source, encoding="utf-8")
            pipeline.build_yomitan_from_human(human, output, TAGS)
            rows = json.loads((output / "yomitan" / "term_bank_1.json").read_text("utf-8"))
        expressions = [row[0] for row in rows]
        self.assertIn("test", expressions)
        self.assertIn("tést", expressions)
        self.assertIn("ujian", expressions)
        serialized = json.dumps(rows, ensure_ascii=False)
        self.assertIn("from the marker intermediate.", serialized)
        self.assertIn("?query=coba", serialized)

    def test_example_placeholders_are_resolved_only_in_layer_two(self) -> None:
        source = (
            "[Entry] abu\n"
            "[Example] – bara\n"
            "[Translation] cinder.\n"
            "\n"
            "[Subentry] abu-abu\n"
            "[Example] udara ~\n"
            "[Translation] an overcast sky.\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            human = temp / "human-readable.txt"
            output = temp / "output"
            human.write_text(source, encoding="utf-8")
            parsed = pipeline.parse_human_intermediate(human)
            self.assertEqual("– bara", parsed[0]["forms"][0]["content"][0]["value"])
            self.assertEqual("udara ~", parsed[0]["forms"][1]["content"][0]["value"])
            pipeline.build_yomitan_from_human(human, output, TAGS)
            rows = json.loads((output / "yomitan" / "term_bank_1.json").read_text("utf-8"))

        root_row = next(row for row in rows if row[0] == "abu")
        subentry_row = next(row for row in rows if row[0] == "abu-abu")
        root_rendered = json.dumps(root_row[5], ensure_ascii=False)
        subentry_rendered = json.dumps(subentry_row[5], ensure_ascii=False)
        self.assertIn("abu bara", root_rendered)
        self.assertNotIn("– bara", root_rendered)
        self.assertIn("udara abu-abu", subentry_rendered)
        self.assertNotIn("udara ~", subentry_rendered)

    def test_optional_forms_are_layer_two_lookup_aliases(self) -> None:
        self.assertEqual(
            ["mengabolisi", "mengabolisikan"],
            pipeline.expand_optional_form("mengabolisi(kan)"),
        )
        self.assertEqual(
            ["abstén", "abstéin"],
            pipeline.expand_optional_form("absté(i)n"),
        )
        self.assertEqual(
            ["mengaduh", "mengaduh-aduh"],
            pipeline.expand_optional_form("mengaduh(-aduh)"),
        )


if __name__ == "__main__":
    unittest.main()
