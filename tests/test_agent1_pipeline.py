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
        debug = pipeline.group_debug_entries(PDF, [21, 25, 70, 236, 500])
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
        self.assertIn("à Rp 1.000,-", self.content_values(accented_a, "example"))

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
            "menganyang hati",
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
            "Soalnya uang belanja dapur terasa menjadi cupet (sempit)",
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
            "tidak memperlihatkan cuping hidungnya",
            self.content_values(cuping, "example"),
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
