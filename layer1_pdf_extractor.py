"""Layer 1: faithfully extract structure and typographic runs from the PDF.

Layer 1 deliberately does not guess Definition/Example/Translation roles.
It retains only constrained lexical structure (entries, homographs, senses,
labels, and arrow-led cross-references) plus the typography actually observed
in the source PDF. Presentation decisions belong to Layer 2.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


WORK_DIR = Path(__file__).resolve().parent
REPO_DIR = WORK_DIR
GROUND_TRUTH_PATH = REPO_DIR / "extract_agent1.py"
EXPECTED_GROUND_TRUTH_SHA256 = (
    "fa37367c55b7d5e5c57b99b77b4a331933fcf1b59e929fcb82545a2cbb18634f"
)
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

import extract_agent1 as source_parser  # noqa: E402


LAYER1_VERSION = "3.5.2"
LINE_WRAP_RESOLUTIONS_PATH = WORK_DIR / "audit1_line_wrap_resolutions.csv"
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
LETTER_PATTERN = r"[^\W\d_]"
WORD_RE = re.compile(rf"{LETTER_PATTERN}+", flags=re.UNICODE)
HYPHENATED_WORD_RE = re.compile(
    rf"(?<!{LETTER_PATTERN})"
    rf"({LETTER_PATTERN}+)-({LETTER_PATTERN}+)"
    rf"(?!{LETTER_PATTERN})",
    flags=re.UNICODE,
)
TRAILING_LINE_WRAP_RE = re.compile(
    rf"({LETTER_PATTERN}+)-\s*$",
    flags=re.UNICODE,
)
LEADING_LINE_WRAP_RE = re.compile(
    rf"^\s*({LETTER_PATTERN}+)",
    flags=re.UNICODE,
)
BODY_COLUMN_LEFT = {0: 54.0, 1: 270.0}
BODY_COLUMN_RIGHT = {0: 246.0, 1: 462.0}
LINE_WRAP_GEOMETRY_TOLERANCE = 2.5
APPLIED_LINE_WRAP_ACTIONS = {"remove_hyphen", "preserve_hyphen"}
MANUAL_LINE_WRAP_ACTIONS = {
    *APPLIED_LINE_WRAP_ACTIONS,
    "leave_unchanged",
}
MARKER_RE = re.compile(r"^\[([^\]]+)\](?:\s(.*))?$")
FORM_MARKERS = {"Entry", "Subentry", "InlineSubentry"}
RUN_MARKERS = {
    "Roman",
    "Italic",
    "Bold",
    "BoldItalic",
    "SmallCaps",
    "Symbol",
}
FALSE_LOOKUP_VARIANTS = {"/", "-", ".", ")", "!", "+", "–.", "1"}

# These are the high-confidence, manually reviewed source repairs from the
# RC1 corpus audit. They are deliberately exact instead of global hyphen
# normalization: suspended forms such as ``far- or nearsightedness`` remain
# untouched.
REVIEWED_RUN_REPAIRS = {
    "dry- cleaned": "dry-cleaned",
    "menda- pat": "mendapat",
    "tu- kang": "tukang",
    "prob- lems": "problems",
    "as- sets": "assets",
    "ex- aminee": "examinee",
    "pur- posely": "purposely",
    "over- land": "overland",
    "stimu- lans": "stimulans",
    "government- appointed": "government-appointed",
    "deep- rooted": "deep-rooted",
    "light- fingered": "light-fingered",
    "pres- ent": "present",
    "con- sume": "consume",
    "develop- ment": "development",
    "produc- tion": "production",
    "atti- tude": "attitude",
    "part- nership": "partnership",
    "asyik- asyoi": "asyik-asyoi",
    "macro- project": "macro-project",
    "kesem- patan": "kesempatan",
    "men- cari": "mencari",
    "factory- produced": "factory-produced",
    "gu- nanya": "gunanya",
    "six- sided": "six-sided",
    "early- retirement": "early-retirement",
    "au- thority": "authority",
    "m encerucup": "mencerucup",
    "– kuda- kuda": "– kuda-kuda",
    "in- patient": "inpatient",
    "– an- gan-angan": "– angan-angan",
    "24- carat": "24-carat",
    "1821- 1837": "1821-1837",
    "82- page": "82-page",
    "lima -delapan": "lima per delapan",
    "completely, very, well- all-,": "completely, very, well-, all-,",
    "terlénggék(- lénggék)": "terlénggék(-lénggék)",
}
REVIEWED_REFERENCE_REPAIRS = {
    "URANG- ARING": "URANG-ARING",
    "COBAK- CABIK": "COBAK-CABIK",
    "BERSELANG- SELANG": "BERSELANG-SELANG",
    "MOGA- MOGA": "MOGA-MOGA",
    "TU- WA-GA": "TU-WA-GA",
    "MAAF- MEMAAFKAN": "MAAF-MEMAAFKAN",
    "KRÉSÉH- PÉSÉH": "KRÉSÉH-PÉSÉH",
    "TERKATUNG- KATUNG": "TERKATUNG-KATUNG",
    "LALU-LALANG/ LANDANG": "LALU-LALANG/LANDANG",
    "LALU- LINTAS": "LALU-LINTAS",
    "ENGAH- ENGAH": "ENGAH-ENGAH",
    "GUNA- GUNA": "GUNA-GUNA",
    "SERBAH- SERBIH": "SERBAH-SERBIH",
    "SLINTAT- SLINTUT": "SLINTAT-SLINTUT",
    "KARÉSÉH- PÉSÉH": "KARÉSÉH-PÉSÉH",
    "DAYA- UPAYA": "DAYA-UPAYA",
    "MEMBE LIAK": "MEMBELIAK",
    "GE DUNG/RUMAH": "GEDUNG, RUMAH",
    "MEN GIRIM": "MENGIRIM",
    "HEN DAKLAH": "HENDAKLAH",
    "KAKITANG AN": "KAKITANGAN",
    "MELIPAT-GAN DAKAN": "MELIPAT-GANDAKAN",
    "MENGU PAMA": "MENGUPAMA",
    "SU WARNABHUMI": "SUWARNABHUMI",
    "TA TANG": "TATANG",
    "P(E) RANAKAN": "P(E)RANAKAN",
    "P(E) RANCIS": "P(E)RANCIS",
    "K(ER) UDUNG": "K(ER)UDUNG",
    "S(EM) ERBAK": "S(EM)ERBAK",
    "MENGG(ER) APAI": "MENGG(ER)APAI",
    "HAUL II2": "HAUL II 2",
}
REVIEWED_VARIANT_REPAIRS = {
    "apriori": ({"a", "priori"}, ["a priori"]),
    "bercerucup": ({"m"}, ["mencerucup"]),
    "geragu": ({"k"}, ["geraguk"]),
    "hérménéutik": ({"a"}, ["hérménéutika"]),
    "OK": ({"O", "K"}, ["O.K."]),
    "PBR": ({"é", "."}, ["Réformasi"]),
    "SH": ({"S", "H"}, ["S.H."]),
    "trapis": ({"t"}, ["trapist"]),
    "vip": ({"P"}, ["V.I.P."]),
}
REVIEWED_MARKER_SEQUENCE_REPAIRS = (
    (
        ("[Italic] ikat-", "[Roman] style headgear permanently sewn in shape."),
        ("[Italic] ikat-style", "[Roman] headgear permanently sewn in shape."),
    ),
    (
        ("[Italic] merbau-", "[Roman] tree, ironwood,"),
        ("[Italic] merbau-tree", "[Roman] , ironwood,"),
    ),
    (
        ("[Italic] suku", "[Roman] -name."),
        ("[Italic] suku-name", "[Roman] ."),
    ),
    (
        ("[Italic] kajang-", "[Roman] covered ship’s cabin."),
        ("[Italic] kajang-covered", "[Roman] ship’s cabin."),
    ),
    (
        ("[Italic] mantri", "[Roman] -ship, office of a"),
        ("[Italic] mantri-ship", "[Roman] , office of a"),
    ),
    (
        ("[Italic] pandan", "[Roman] -like epiphyte,"),
        ("[Italic] pandan-like", "[Roman] epiphyte,"),
    ),
    (
        ("[Italic] rambutan", "[Roman] -like fruit,"),
        ("[Italic] rambutan-like", "[Roman] fruit,"),
    ),
    (
        ("[Italic] opelét", "[Roman] -drivers."),
        ("[Italic] opelét-drivers", "[Roman] ."),
    ),
    (
        ("[Italic] bécak", "[Roman] -driver."),
        ("[Italic] bécak-driver", "[Roman] ."),
    ),
    (
        ("[Italic] wali-", "[Roman] mayor."),
        ("[Italic] wali kota", "[Roman] mayor."),
    ),
    (
        ("[Roman] pre-", "[Italic] Lebaran", "[Roman] ;"),
        ("[Roman] pre-Lebaran;",),
    ),
    (
        ("[Italic] wereng-", "[Roman] proof Superior Variety, i.e. a rice variety from the"),
        ("[Italic] wereng-proof", "[Roman] Superior Variety, i.e. a rice variety from the"),
    ),
    (
        ("[See] ID-", "[Italic] ul", "[Italic] fitri"),
        ("[See] IDULFITRI",),
    ),
    (
        ("[See] INGGANG-", "[Bold] inggung"),
        ("[See] INGGANG-inggung",),
    ),
    (
        ("[See] BYAR-", "[Italic] pet"),
        ("[See] BYAR-PET",),
    ),
    (
        ("[Subentry] terambung(-ambung", "[Roman] )"),
        ("[Subentry] terambung(-ambung)",),
    ),
    (
        ("[Entry] badani(ah", "[Roman] ) ("),
        ("[Entry] badani(ah)", "[Roman] ("),
    ),
    (
        (
            "[Subentry] berbahas(-bahasan",
            "[Roman] ) to discuss, hold a discussion, (have a) debate, argue.",
        ),
        (
            "[Subentry] berbahas(-bahasan)",
            "[Roman] to discuss, hold a discussion, (have a) debate, argue.",
        ),
    ),
    (
        (
            "[Variant] batéla)",
            "[Homograph] III",
            "[InlineSubentry] (= batél",
            "[Roman] and",
            "[Bold] batéla)",
            "[Roman] (",
        ),
        (
            "[Variant] batél",
            "[Variant] batéla",
            "[Homograph] III",
            "[Roman] (=",
            "[Bold] batél",
            "[Roman] and",
            "[Bold] batéla",
            "[Roman] ) (",
        ),
    ),
    (
        ("[Variant] bekaman)",),
        ("[Variant] bekaman",),
    ),
    (
        ("[Subentry] berbeli-beli(an", "[Roman] )"),
        ("[Subentry] berbeli-beli(an)",),
    ),
    (
        ("[Entry] bloknot(es", "[Roman] ) ("),
        ("[Entry] bloknot(es)", "[Roman] ("),
    ),
    (
        ("[Variant] nyureng (",),
        ("[Variant] nyureng",),
    ),
    (
        ("[Variant] ) menggemik",),
        ("[Variant] menggemik",),
    ),
    (
        (
            "[Subentry] berkerut(-kerut",
            "[Variant] terkerut",
            "[Roman] ) and",
        ),
        (
            "[Subentry] berkerut(-kerut)",
            "[Variant] terkerut",
            "[Roman] and",
        ),
    ),
    (
        ("[Variant] terlénggék(-",),
        ("[Variant] terlénggék(-lénggék)",),
    ),
    (
        ("[Entry] limpap berlimpap(-limpap", "[Roman] )"),
        (
            "[Entry] limpap",
            "[InlineSubentry] berlimpap(-limpap)",
        ),
    ),
    (
        ("[Subentry] bernyut(-nyut", "[Roman] ) to throb."),
        ("[Subentry] bernyut(-nyut)", "[Roman] to throb."),
    ),
    (
        ("[Subentry] merékonstruksi(kan", "[Roman] ) to reconstruct."),
        ("[Subentry] merékonstruksi(kan)", "[Roman] to reconstruct."),
    ),
    (
        ("[Subentry] beriak(-riak", "[Roman] )"),
        ("[Subentry] beriak(-riak)",),
    ),
    (
        ("[Variant] kesémanan)",),
        ("[Variant] kesémanan",),
    ),
    (
        ("[Variant] ) bertaaziah",),
        ("[Variant] bertaaziah",),
    ),
    (
        ("[Subentry] bertelau(-telau", "[Roman] )"),
        ("[Subentry] bertelau(-telau)",),
    ),
    (
        (
            "[Entry] andosan, andoseman",
            "[Variant] andosir",
            "[Roman] and",
            "[Bold] andosir",
        ),
        (
            "[Entry] andosan",
            "[Variant] andoseman",
            "[Variant] andosir",
            "[Roman] ,",
            "[Bold] andoseman",
            "[Roman] and",
            "[Bold] andosir",
        ),
    ),
    (
        (
            "[Subentry] mengasin, mengasini",
            "[Variant] mengasinkan",
            "[Roman] and",
            "[Bold] mengasinkan 1",
        ),
        (
            "[Subentry] mengasin",
            "[Variant] mengasini",
            "[Variant] mengasinkan",
            "[Roman] ,",
            "[Bold] mengasini",
            "[Roman] and",
            "[Bold] mengasinkan 1",
        ),
    ),
    (
        (
            "[Subentry] mengasin, mengasini",
            "[Variant] mengasinkan",
            "[Roman] and",
            "[Bold] mengasinkan",
        ),
        (
            "[Subentry] mengasin",
            "[Variant] mengasini",
            "[Variant] mengasinkan",
            "[Roman] ,",
            "[Bold] mengasini",
            "[Roman] and",
            "[Bold] mengasinkan",
        ),
    ),
    (
        ("[Variant] in de hooi, in de hooy, in de hoy,",),
        (
            "[Variant] in de hooi",
            "[Variant] in de hooy",
            "[Variant] in de hoy",
        ),
    ),
    (
        (
            "[Entry] jrang, jréng",
            "[Variant] jrung",
            "[Roman] ,",
            "[Bold] jrung",
        ),
        (
            "[Entry] jrang",
            "[Variant] jréng",
            "[Variant] jrung",
            "[Roman] ,",
            "[Bold] jréng",
            "[Roman] ,",
            "[Bold] jrung",
        ),
    ),
    (
        (
            "[Entry] jumrah, jumrat,",
            "[Variant] jumroh",
            "[Roman] and",
            "[Bold] jumroh",
        ),
        (
            "[Entry] jumrah",
            "[Variant] jumrat",
            "[Variant] jumroh",
            "[Roman] ,",
            "[Bold] jumrat",
            "[Roman] , and",
            "[Bold] jumroh",
        ),
    ),
    (
        (
            "[Subentry] mengutik(-ngutik), mengkutik(-kutik)",
            "[Variant] ngutik-ngutik",
            "[Roman] and",
            "[Bold] ngutik-ngutik",
        ),
        (
            "[Subentry] mengutik(-ngutik)",
            "[Variant] mengkutik(-kutik)",
            "[Variant] ngutik-ngutik",
            "[Roman] ,",
            "[Bold] mengkutik(-kutik)",
            "[Roman] and",
            "[Bold] ngutik-ngutik",
        ),
    ),
    (
        (
            "[Italic] sebagai/seperti ular dikutik ékor",
            "[Roman] to start (with fright/anger/out of one’s sleep).",
            "[Italic] ~ gigi",
        ),
        (
            "[Italic] sebagai/seperti ular dikutik ékor",
            "[Roman] to start (with fright/anger/out of one’s sleep).",
            "[Italic] mengutik(-ngutik), mengkutik(-kutik) gigi",
        ),
    ),
    (
        ("[Subentry] selingkung:",),
        ("[Subentry] selingkung", "[Roman] :"),
    ),
    (
        ("[Variant] magfirat,",),
        ("[Variant] magfirat",),
    ),
    (
        (
            "[Entry] napza, Napza",
            "[Variant] NAPZA",
            "[Roman] and",
            "[Bold] NAPZA",
        ),
        (
            "[Entry] napza",
            "[Variant] Napza",
            "[Variant] NAPZA",
            "[Roman] ,",
            "[Bold] Napza",
            "[Roman] and",
            "[Bold] NAPZA",
        ),
    ),
    (
        ("[Variant] ontogénése,",),
        ("[Variant] ontogénése",),
    ),
    (
        (
            "[Subentry] keopénan, kopén,",
            "[Variant] kopénan",
            "[Roman] and",
            "[Bold] kopénan 1",
        ),
        (
            "[Subentry] keopénan",
            "[Variant] kopén",
            "[Variant] kopénan",
            "[Roman] ,",
            "[Bold] kopén",
            "[Roman] and",
            "[Bold] kopénan 1",
        ),
    ),
    (
        (
            "[Subentry] keopénan, kopén,",
            "[Variant] kopénan",
            "[Roman] and",
            "[Bold] kopénan",
        ),
        (
            "[Subentry] keopénan",
            "[Variant] kopén",
            "[Variant] kopénan",
            "[Roman] ,",
            "[Bold] kopén",
            "[Roman] and",
            "[Bold] kopénan",
        ),
    ),
    (
        (
            "[Entry] old",
            "[Variant] new",
            "[Variant] berold-and-new",
            "[Roman] (",
            "[Label] E",
            "[Roman] )",
            "[Bold] berold-and-new",
        ),
        (
            "[Entry] old and new",
            "[InlineSubentry] berold-and-new",
            "[Roman] (",
            "[Label] E",
            "[Roman] )",
        ),
    ),
    (
        (
            "[Entry] i and",
            "[Homograph] I",
            "[InlineSubentry] I",
        ),
        (
            "[Entry] i",
            "[Variant] I",
            "[Homograph] I",
        ),
    ),
    (
        (
            "[Entry] v and",
            "[Homograph] V",
            "[InlineSubentry] I",
        ),
        (
            "[Entry] v",
            "[Variant] V",
            "[Homograph] I",
        ),
    ),
    (
        ("[Entry] x and", "[Homograph] X"),
        ("[Entry] x", "[Variant] X"),
    ),
    (
        (
            "[Roman] /éks/ the 24th letter of the Latin alphabet used for writing Indonesian (used only in spelling some foreign words and slang).",
            "[Bold] Y",
            "[Entry] y",
        ),
        (
            "[Roman] /éks/ the 24th letter of the Latin alphabet used for writing Indonesian (used only in spelling some foreign words and slang).",
            "[Entry] y",
        ),
    ),
    (
        ("[Italic] de", "[Roman] - and", "[Italic] dis-"),
        ("[Italic] de-", "[Roman] and", "[Italic] dis-"),
    ),
    (
        (
            "[Roman] ) to cause to, let, make (often equivalent to verb with suffix -",
            "[Italic] kan",
            "[Roman] in standard language).",
        ),
        (
            "[Roman] ) to cause to, let, make (often equivalent to verb with suffix",
            "[Italic] -kan",
            "[Roman] in standard language).",
        ),
    ),
    (
        ("[Bold] 5 -", "[Roman] borne."),
        ("[Sense] 5", "[Roman] -borne."),
    ),
    (
        (
            "[Bold] ny",
            "[Roman] - also see entries beginning with",
            "[Bold] c",
            "[Roman] - or",
            "[Bold] s-",
        ),
        (
            "[Bold] ny-",
            "[Roman] also see entries beginning with",
            "[Bold] c-",
            "[Roman] or",
            "[Bold] s-",
        ),
    ),
    (
        (
            "[Roman] ) particle which precedes the verb, which often contains an infix -",
            "[Italic] er",
            "[Roman] - or -",
            "[Italic] el",
            "[Roman] -; it indicates that the subject is plural and/ or that the action is repeated, multiple, or chaotic.",
        ),
        (
            "[Roman] ) particle which precedes the verb, which often contains an infix",
            "[Italic] -er-",
            "[Roman] or",
            "[Italic] -el-",
            "[Roman] ; it indicates that the subject is plural and/ or that the action is repeated, multiple, or chaotic.",
        ),
    ),
    (
        (
            "[Entry] pl",
            "[Roman] - also see entries beginning with",
            "[Bold] pel-",
        ),
        (
            "[Entry] pl-",
            "[Roman] also see entries beginning with",
            "[Bold] pel-",
        ),
    ),
    (
        ("[See] PASCA",),
        ("[See] PASCA-",),
    ),
)


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
        "line_wrap_audit": (
            intermediate_dir / f"audit1_line_wrap_{profile}.json"
        ),
        "manifest": intermediate_dir / f"layer1_{profile}_manifest.json",
    }


def _line_wrap_key(
    previous_line_id: str,
    next_line_id: str,
    left_fragment: str,
    right_fragment: str,
) -> str:
    return "|".join(
        (
            previous_line_id,
            next_line_id,
            left_fragment.casefold(),
            right_fragment.casefold(),
        )
    )


def _line_wrap_word_evidence(
    debug_entries: list[dict[str, Any]],
) -> dict[str, set[str]]:
    """Collect source-attested joined and hyphenated word spellings."""
    words: set[str] = set()
    hyphenated_words: set[str] = set()
    for debug_entry in debug_entries:
        for form in debug_entry.get("forms", []):
            for line in form.get("lines", []):
                text = str(line.get("clean_text", ""))
                words.update(
                    match.group(0).casefold()
                    for match in WORD_RE.finditer(text)
                )
                hyphenated_words.update(
                    f"{match.group(1)}-{match.group(2)}".casefold()
                    for match in HYPHENATED_WORD_RE.finditer(text)
                )
    return {
        "words": words,
        "hyphenated_words": hyphenated_words,
    }


def _load_line_wrap_resolutions(
    path: Path,
) -> dict[str, Any]:
    """Load reviewed decisions while retaining pending rows for audit."""
    if not path.is_file():
        raise FileNotFoundError(
            f"Layer 1 line-wrap resolution table is missing: {path}"
        )

    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    required = {
        "resolution_key",
        "previous_line_id",
        "next_line_id",
        "left_fragment",
        "right_fragment",
        "joined_candidate",
        "hyphenated_candidate",
        "approved_resolution",
        "review_status",
    }
    missing = required.difference(rows[0] if rows else ())
    if missing:
        raise ValueError(
            "Line-wrap resolution CSV is missing columns: "
            + ", ".join(sorted(missing))
        )

    by_key: dict[str, dict[str, str]] = {}
    approved: dict[str, dict[str, str]] = {}
    for row_number, row in enumerate(rows, start=2):
        expected_key = _line_wrap_key(
            row["previous_line_id"],
            row["next_line_id"],
            row["left_fragment"],
            row["right_fragment"],
        )
        key = row["resolution_key"].strip()
        if key != expected_key:
            raise ValueError(
                f"Line-wrap CSV row {row_number} has an invalid key: {key!r}"
            )
        if key in by_key:
            raise ValueError(
                f"Duplicate line-wrap resolution key on row {row_number}: "
                f"{key}"
            )
        by_key[key] = row

        if row["review_status"].strip().casefold() != "approved":
            continue
        resolution = row["approved_resolution"].strip()
        if resolution not in MANUAL_LINE_WRAP_ACTIONS:
            raise ValueError(
                f"Approved line-wrap CSV row {row_number} has invalid "
                f"resolution {resolution!r}"
            )
        approved[key] = row

    return {
        "path": path,
        "rows": by_key,
        "approved": approved,
    }


def _ascii_line_wrap_decisions(
    form: dict[str, Any],
    evidence: dict[str, set[str]] | None,
    resolutions: dict[str, Any] | None = None,
) -> dict[int, dict[str, Any]]:
    """Classify high-confidence ASCII-hyphen wraps between PDF lines.

    Candidates must end at a known body-column edge, resume at that column's
    left edge, remain on the same page, and retain the same source style.
    Attested corpus spellings take precedence. Approved CSV decisions are
    consulted only when corpus evidence cannot decide.
    """
    if evidence is None:
        return {}

    words = evidence["words"]
    hyphenated_words = evidence["hyphenated_words"]
    approved = (resolutions or {}).get("approved", {})
    lines = form.get("lines", [])
    decisions: dict[int, dict[str, Any]] = {}
    for next_index in range(1, len(lines)):
        previous_line = lines[next_index - 1]
        next_line = lines[next_index]
        if (
            previous_line.get("pdf_page") != next_line.get("pdf_page")
            or previous_line.get("column") != next_line.get("column")
        ):
            continue

        previous_spans = [
            span
            for span in previous_line.get("spans", [])
            if str(span.get("clean_text", "")).strip()
        ]
        next_spans = [
            span
            for span in next_line.get("spans", [])
            if str(span.get("clean_text", "")).strip()
        ]
        if not previous_spans or not next_spans:
            continue
        previous_span = previous_spans[-1]
        next_span = next_spans[0]
        if previous_span.get("style") != next_span.get("style"):
            continue

        trailing = TRAILING_LINE_WRAP_RE.search(
            str(previous_span.get("clean_text", ""))
        )
        leading = LEADING_LINE_WRAP_RE.match(
            str(next_span.get("clean_text", ""))
        )
        if trailing is None or leading is None:
            continue

        column = int(previous_line["column"])
        previous_bbox = previous_span.get("bbox", [])
        next_bbox = next_span.get("bbox", [])
        if len(previous_bbox) < 3 or not next_bbox:
            continue
        if (
            abs(float(previous_bbox[2]) - BODY_COLUMN_RIGHT[column])
            > LINE_WRAP_GEOMETRY_TOLERANCE
            or abs(float(next_bbox[0]) - BODY_COLUMN_LEFT[column])
            > LINE_WRAP_GEOMETRY_TOLERANCE
        ):
            continue

        left_fragment = trailing.group(1)
        right_fragment = leading.group(1)
        joined = f"{left_fragment}{right_fragment}"
        hyphenated = f"{left_fragment}-{right_fragment}"
        key = _line_wrap_key(
            str(previous_line["line_id"]),
            str(next_line["line_id"]),
            left_fragment,
            right_fragment,
        )
        manual_row = approved.get(key)

        if hyphenated.casefold() in hyphenated_words:
            action = "preserve_hyphen"
            source = "corpus_hyphenated"
        elif joined.casefold() in words:
            action = "remove_hyphen"
            source = "corpus_joined"
        elif manual_row is None:
            action = "ambiguous"
            source = "unresolved"
        elif (
            manual_row["joined_candidate"] != joined
            or manual_row["hyphenated_candidate"] != hyphenated
        ):
            action = "conflict"
            source = "manual_csv_mismatch"
        else:
            action = manual_row["approved_resolution"].strip()
            source = "manual_csv"

        replacement = None
        if action == "remove_hyphen":
            replacement = joined
        elif action == "preserve_hyphen":
            replacement = hyphenated

        decisions[next_index] = {
            "resolution_key": key,
            "previous_line_id": previous_line["line_id"],
            "next_line_id": next_line["line_id"],
            "pdf_page": previous_line["pdf_page"],
            "column": column,
            "style": previous_span["style"],
            "left_fragment": left_fragment,
            "right_fragment": right_fragment,
            "printed": f"{left_fragment}- {right_fragment}",
            "joined_candidate": joined,
            "hyphenated_candidate": hyphenated,
            "action": action,
            "source": source,
            "replacement": replacement,
        }
    return decisions


def _line_wrap_audit(
    debug_entries: list[dict[str, Any]],
    evidence: dict[str, set[str]],
    resolutions: dict[str, Any],
    *,
    profile: str,
) -> dict[str, Any]:
    """Return a persistent audit of repaired and unresolved line wraps."""
    decisions: list[dict[str, Any]] = []
    for debug_entry in debug_entries:
        forms = debug_entry.get("forms", [])
        root_expression = ""
        if forms:
            root_expression = str(
                forms[0].get("expression_parse", {}).get("expression", "")
            )
        for form in forms:
            form_expression = str(
                form.get("expression_parse", {}).get("expression", "")
            )
            for decision in _ascii_line_wrap_decisions(
                form,
                evidence,
                resolutions,
            ).values():
                decisions.append(
                    {
                        "entry": root_expression,
                        "form": form_expression,
                        **decision,
                    }
                )

    repairs = [
        decision
        for decision in decisions
        if decision["action"] in APPLIED_LINE_WRAP_ACTIONS
    ]
    ambiguous = [
        decision
        for decision in decisions
        if decision["action"] == "ambiguous"
    ]
    conflicts = [
        decision
        for decision in decisions
        if decision["action"] == "conflict"
    ]
    unchanged = [
        decision
        for decision in decisions
        if decision["action"] == "leave_unchanged"
    ]
    encountered_keys = {
        str(decision["resolution_key"]) for decision in decisions
    }
    csv_rows = resolutions["rows"]
    csv_approved = resolutions["approved"]
    unmatched = (
        sorted(set(csv_rows).difference(encountered_keys))
        if profile == "full"
        else []
    )

    def action_count(value: str) -> int:
        return sum(
            decision["action"] == value for decision in decisions
        )

    def source_count(value: str) -> int:
        return sum(
            decision["source"] == value for decision in decisions
        )

    return {
        "schema": "acomprehensive-line-wrap-audit-1",
        "profile": profile,
        "policy": (
            "Repair only same-style ASCII-hyphen wraps at verified PDF "
            "column boundaries. Corpus spellings decide first. Approved CSV "
            "rows may resolve otherwise ambiguous candidates; pending rows "
            "never affect the intermediate."
        ),
        "resolution_csv": str(resolutions["path"].resolve()),
        "counts": {
            "candidates": len(decisions),
            "remove_hyphen": action_count("remove_hyphen"),
            "preserve_hyphen": action_count("preserve_hyphen"),
            "approved_leave_unchanged": len(unchanged),
            "ambiguous_unchanged": len(ambiguous),
            "conflicts": len(conflicts),
            "corpus_joined": source_count("corpus_joined"),
            "corpus_hyphenated": source_count("corpus_hyphenated"),
            "manual_csv_applied": source_count("manual_csv"),
            "csv_rows": len(csv_rows),
            "csv_approved": len(csv_approved),
            "csv_pending": len(csv_rows) - len(csv_approved),
            "csv_unmatched_full_profile": len(unmatched),
        },
        "repairs": repairs,
        "approved_unchanged": unchanged,
        "ambiguous": ambiguous,
        "conflicts": conflicts,
        "csv_unmatched_full_profile": unmatched,
    }


def _span_events(
    span: dict[str, Any],
    tag_map: dict[str, dict[str, str]],
    *,
    protect_label: bool = False,
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

    if style in {"italic", "bold_italic"} and not protect_label:
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


def _merge_punctuated_abbreviation_spans(
    spans: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Consolidate italic initials such as ``A.S.`` before tag detection.

    The PDF stores each initial in italic but each period in Roman. Since
    single initials can also be valid source-tag codes, classifying one span
    at a time turns abbreviations into unrelated language labels. A pair of
    italic capital initials separated and followed by Roman periods is strong
    enough context to retain the complete abbreviation as one italic run.
    Consolidation also prevents the marker format from inserting spaces into
    ``A.S.`` while retaining spaces that are present in forms such as
    ``K. H.``.
    """
    meaningful = [
        index
        for index, span in enumerate(spans)
        if str(span.get("clean_text", "")).strip()
    ]
    groups: list[tuple[int, int, re.Match[str]]] = []

    def italic_initial(index: int) -> bool:
        span = spans[index]
        return bool(
            span.get("style") in {"italic", "bold_italic"}
            and re.fullmatch(
                r"[A-Z]",
                str(span.get("clean_text", "")).strip(),
            )
        )

    def roman_period(index: int) -> bool:
        span = spans[index]
        return bool(
            span.get("style") == "roman"
            and re.fullmatch(
                r"\s*\.\s*",
                str(span.get("clean_text", "")),
            )
        )

    position = 0
    while position + 3 < len(meaningful):
        first_index = meaningful[position]
        separator_index = meaningful[position + 1]
        second_index = meaningful[position + 2]
        if not (
            italic_initial(first_index)
            and roman_period(separator_index)
            and italic_initial(second_index)
        ):
            position += 1
            continue

        cursor = position + 2
        while (
            cursor + 2 < len(meaningful)
            and roman_period(meaningful[cursor + 1])
            and italic_initial(meaningful[cursor + 2])
        ):
            cursor += 2
        if cursor + 1 >= len(meaningful):
            position += 1
            continue

        suffix_index = meaningful[cursor + 1]
        suffix = spans[suffix_index]
        suffix_match = (
            re.match(r"^\s*\.", str(suffix.get("clean_text", "")))
            if suffix.get("style") == "roman"
            else None
        )
        if suffix_match is None:
            position += 1
            continue
        groups.append((first_index, suffix_index, suffix_match))
        position = cursor + 2

    if not groups:
        return spans

    output: list[dict[str, Any]] = []
    group_by_start = {start: (end, match) for start, end, match in groups}
    index = 0
    while index < len(spans):
        group = group_by_start.get(index)
        if group is None:
            output.append(spans[index])
            index += 1
            continue

        end_index, suffix_match = group
        abbreviation = "".join(
            str(span.get("clean_text", ""))
            for span in spans[index:end_index]
        )
        suffix_text = str(spans[end_index].get("clean_text", ""))
        abbreviation += suffix_text[: suffix_match.end()]
        output.append(
            {
                **spans[index],
                "clean_text": source_parser.clean_text(abbreviation),
            }
        )
        suffix_remainder = suffix_text[suffix_match.end() :]
        if suffix_remainder:
            output.append(
                {
                    **spans[end_index],
                    "clean_text": suffix_remainder,
                }
            )
        index = end_index + 1
    return output


def _template_operand_span_indices(
    spans: list[dict[str, Any]],
    tag_map: dict[str, dict[str, str]],
    previous_span: dict[str, Any] | None = None,
) -> set[int]:
    """Protect tag-like words that are operands of ``~``/``–`` templates.

    Source tags may also be ordinary lexical material: ``ling`` is both the
    Linguistics tag and the first line-wrapped fragment of ``lingkungan``;
    ``bio``, ``mil``, and single initials have similar collisions. An italic
    token immediately following an unparenthesized template operator is the
    operator's lexical operand, not a usage label. Parenthesized labels remain
    unaffected because their preceding Roman span ends in ``(``, not in the
    operator.
    """
    meaningful = [
        index
        for index, span in enumerate(spans)
        if str(span.get("clean_text", "")).strip()
    ]
    protected: set[int] = set()

    def follows_template_operator(
        previous: dict[str, Any],
        candidate: dict[str, Any],
    ) -> bool:
        candidate_text = str(candidate.get("clean_text", "")).strip()
        return bool(
            previous.get("style") == "roman"
            and re.search(
                r"[~–]\s*$",
                str(previous.get("clean_text", "")),
            )
            and candidate.get("style") in {"italic", "bold_italic"}
            and source_parser.tag_codes(candidate_text, tag_map)
        )

    if meaningful and previous_span is not None:
        first_index = meaningful[0]
        if follows_template_operator(previous_span, spans[first_index]):
            protected.add(first_index)

    for position in range(1, len(meaningful)):
        previous_index = meaningful[position - 1]
        candidate_index = meaningful[position]
        previous = spans[previous_index]
        candidate = spans[candidate_index]
        if not follows_template_operator(previous, candidate):
            continue
        protected.add(candidate_index)
    return protected


def _raw_content_events(
    form: dict[str, Any],
    zone: dict[str, Any],
    parsed_zone: dict[str, Any],
    tag_map: dict[str, dict[str, str]],
    line_wrap_evidence: dict[str, set[str]] | None = None,
    line_wrap_resolutions: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    lines = form["lines"]
    previous_effective_span: dict[str, Any] | None = None
    line_wrap_decisions = _ascii_line_wrap_decisions(
        form,
        line_wrap_evidence,
        line_wrap_resolutions,
    )
    applied_repairs = [
        decision
        for decision in line_wrap_decisions.values()
        if decision["action"] in APPLIED_LINE_WRAP_ACTIONS
    ]
    if applied_repairs:
        form["line_wrap_repairs"] = applied_repairs
    else:
        form.pop("line_wrap_repairs", None)

    for line_index, line in enumerate(lines):
        spans = zone["remaining_spans"] if line_index == 0 else line["spans"]
        repair_before = line_wrap_decisions.get(line_index)
        repair_after = line_wrap_decisions.get(line_index + 1)
        last_meaningful_span = next(
            (
                index
                for index in range(len(spans) - 1, -1, -1)
                if str(spans[index].get("clean_text", "")).strip()
            ),
            None,
        )
        first_event_on_line = True
        line_boundary = ""
        if line_index:
            line_boundary = (
                ""
                if (
                    lines[line_index - 1]["join_next_without_space"]
                    or (
                        repair_before is not None
                        and repair_before["action"]
                        in APPLIED_LINE_WRAP_ACTIONS
                    )
                )
                else " "
            )
        effective_spans = list(spans)
        if (
            repair_after is not None
            and repair_after["action"] == "remove_hyphen"
            and last_meaningful_span is not None
        ):
            effective_spans[last_meaningful_span] = {
                **effective_spans[last_meaningful_span],
                "clean_text": re.sub(
                    r"-\s*$",
                    "",
                    str(effective_spans[last_meaningful_span]["clean_text"]),
                ),
            }
        effective_spans = _merge_punctuated_abbreviation_spans(
            effective_spans
        )
        protected_label_spans = _template_operand_span_indices(
            effective_spans,
            tag_map,
            previous_effective_span,
        )
        for span_index, span in enumerate(effective_spans):
            for event in _span_events(
                span,
                tag_map,
                protect_label=span_index in protected_label_spans,
            ):
                if first_event_on_line:
                    event["boundary"] = line_boundary
                    first_event_on_line = False
                events.append(event)
        previous_effective_span = next(
            (
                span
                for span in reversed(effective_spans)
                if str(span.get("clean_text", "")).strip()
            ),
            None,
        )

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

        target_parts: list[tuple[str, str]] = []
        trailing_operator: dict[str, Any] | None = None
        last_target_end = cursor
        while (
            cursor < len(events)
            and events[cursor]["kind"] == "run"
            and events[cursor]["style"] == "small_caps"
        ):
            value = source_parser.clean_text(str(events[cursor]["value"]))
            if value:
                operator_match = re.fullmatch(
                    r"(.*[.;:!?])\s+(–)",
                    value,
                )
                if operator_match is not None:
                    value = operator_match.group(1)
                    trailing_operator = {
                        "kind": "run",
                        "style": "roman",
                        "value": operator_match.group(2),
                        "boundary": "",
                    }
                target_parts.append(
                    (value, str(events[cursor].get("boundary", "")))
                )
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

        # A bold number directly attached to an arrow target is a target
        # sense suffix, not a new sense in the current entry. The following
        # Roman period remains source punctuation.
        suffix_cursor = last_target_end
        while (
            target_parts
            and suffix_cursor < len(events)
            and events[suffix_cursor]["kind"] == "run"
            and not str(events[suffix_cursor]["value"]).strip()
        ):
            suffix_cursor += 1
        if (
            target_parts
            and not re.search(r"[.;:!?]\s*$", target_parts[-1][0])
            and suffix_cursor < len(events)
            and events[suffix_cursor]["kind"] == "sense"
        ):
            after_suffix = suffix_cursor + 1
            while (
                after_suffix < len(events)
                and events[after_suffix]["kind"] == "run"
                and not str(events[after_suffix]["value"]).strip()
            ):
                after_suffix += 1
            if (
                after_suffix < len(events)
                and events[after_suffix]["kind"] == "run"
                and events[after_suffix].get("style") == "roman"
                and re.match(
                    r"^\s*[.,;:!?)]",
                    str(events[after_suffix].get("value", "")),
                )
            ):
                target_parts.append(
                    (
                        str(events[suffix_cursor]["value"]),
                        " ",
                    )
                )
                last_target_end = suffix_cursor + 1

        if target_parts:
            target = ""
            for value, boundary in target_parts:
                separator = boundary
                if (
                    target
                    and not separator
                    and not target.endswith(("-", "/", "'", "’"))
                ):
                    separator = " "
                target = source_parser.join_piece(target, value, separator)
            output.append(
                {
                    "kind": "see",
                    "value": target,
                    "boundary": str(event.get("boundary", "")),
                }
            )
            if trailing_operator is not None:
                output.append(trailing_operator)
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


def _demote_reference_suffix_senses(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Retain a non-arrow ``SMALL CAPS 2.`` reference on the same line."""
    output: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        previous = output[-1] if output else None
        following = events[index + 1] if index + 1 < len(events) else None
        before_previous = output[-2] if len(output) >= 2 else None
        if (
            event["kind"] == "sense"
            and previous is not None
            and previous["kind"] == "run"
            and previous.get("style") == "small_caps"
            and before_previous is not None
            and before_previous["kind"] == "run"
            and before_previous.get("style") == "roman"
            and re.search(
                r"\bof\s*$",
                str(before_previous.get("value", "")),
                flags=re.I,
            )
            and following is not None
            and following["kind"] == "run"
            and following.get("style") == "roman"
            and re.match(
                r"^\s*\.(?!\.)",
                str(following.get("value", "")),
            )
        ):
            output.append(
                {
                    "kind": "run",
                    "style": "bold",
                    "value": str(event["value"]),
                    "boundary": str(event.get("boundary", "")),
                }
            )
            continue
        output.append(event)
    return output


def _strip_redundant_sense_delimiters(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Remove a source period already represented by a ``sense`` event."""
    output: list[dict[str, Any]] = []
    for event in events:
        if (
            event["kind"] == "run"
            and event.get("style") == "roman"
            and output
            and output[-1]["kind"] == "sense"
        ):
            value = str(event.get("value", ""))
            delimiter = re.match(
                r"^\s*\.(?!\.)(?:\s+|(?=\()|$)",
                value,
            )
            if delimiter is not None:
                remainder = value[delimiter.end() :].lstrip()
                if remainder:
                    output.append({**event, "value": remainder})
                continue
        output.append(event)
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


def _split_parenthesized_template_operators(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Italicize a template operator before a confirmed parenthetical phrase.

    This recognizes only a Roman run ending in ``~ (`` or ``– (``, followed
    immediately by italic content whose closing parenthesis is either in that
    italic run or at the start of the next Roman run. It moves only the
    operator into its own italic run and retains the observed parenthesis
    styles. Other parentheses, ellipses, labels, and mixed prose are left
    untouched.
    """
    output: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        if event["kind"] != "run" or event.get("style") != "roman":
            output.append(event)
            continue

        value = str(event.get("value", ""))
        match = re.search(r"([~–])\s*\(\s*$", value)
        following = events[index + 1] if index + 1 < len(events) else None
        if not (
            match is not None
            and following is not None
            and following["kind"] == "run"
            and following.get("style") == "italic"
        ):
            output.append(event)
            continue

        italic_value = str(following.get("value", ""))
        closes_in_italic = ")" in italic_value
        after_italic = (
            events[index + 2] if index + 2 < len(events) else None
        )
        closes_in_roman = bool(
            after_italic is not None
            and after_italic["kind"] == "run"
            and after_italic.get("style") == "roman"
            and re.match(r"^\s*\)", str(after_italic.get("value", "")))
        )
        if not (closes_in_italic or closes_in_roman):
            output.append(event)
            continue

        prefix = value[: match.start()].rstrip()
        original_boundary = str(event.get("boundary", ""))
        if prefix:
            output.append({**event, "value": prefix})
            operator_boundary = ""
        else:
            operator_boundary = original_boundary
        output.extend(
            [
                {
                    "kind": "run",
                    "style": "italic",
                    "value": match.group(1),
                    "boundary": operator_boundary,
                },
                {
                    "kind": "run",
                    "style": "roman",
                    "value": "(",
                    "boundary": "",
                },
            ]
        )
    return output


def _attach_boundary_operators_to_italics(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Move boundary ``~``/``–`` placeholders into adjacent italic runs.

    The PDF occasionally isolates a lexical placeholder in the roman font
    even though it belongs to the neighboring italic phrase. Layer 1 repairs
    only direct boundaries: a trailing operator moves to the following italic
    run, and a leading operator moves to the preceding italic run. A
    standalone operator prefers the following italic run, matching the
    dictionary's common ``~ phrase``/``– phrase`` template.
    """
    output = [dict(event) for event in events]

    def italic_run(index: int) -> bool:
        return (
            0 <= index < len(output)
            and output[index]["kind"] == "run"
            and output[index].get("style") == "italic"
        )

    def operator_text(value: str) -> str:
        operators = re.findall(r"[~–]", value)
        if operators and len(set(operators)) == 1:
            return operators[0]
        return " ".join(operators)

    def prefix_italic(index: int, operator: str) -> None:
        existing = str(output[index]["value"])
        if existing.lstrip().startswith(operator):
            return
        output[index]["value"] = source_parser.clean_text(
            f"{operator} {existing}"
        )

    def suffix_italic(index: int, operator: str) -> None:
        existing = str(output[index]["value"])
        if existing.rstrip().endswith(operator):
            return
        output[index]["value"] = source_parser.clean_text(
            f"{existing} {operator}"
        )

    for index, event in enumerate(output):
        if event["kind"] != "run" or event.get("style") != "roman":
            continue
        value = str(event.get("value", ""))
        stripped = value.strip()

        standalone = re.fullmatch(r"[~–](?:\s*[~–])*", stripped)
        if standalone is not None:
            operator = operator_text(standalone.group(0))
            if italic_run(index + 1):
                prefix_italic(index + 1, operator)
                event["value"] = ""
            elif italic_run(index - 1):
                suffix_italic(index - 1, operator)
                event["value"] = ""
            continue

        trailing = re.search(r"([~–](?:\s*[~–])*)\s*$", value)
        if trailing is not None and italic_run(index + 1):
            operator = operator_text(trailing.group(1))
            event["value"] = value[: trailing.start()].rstrip()
            prefix_italic(index + 1, operator)
            value = str(event["value"])

        leading = re.match(r"^\s*([~–](?:\s*[~–])*)", value)
        if leading is not None and italic_run(index - 1):
            operator = operator_text(leading.group(1))
            suffix_italic(index - 1, operator)
            event["value"] = value[leading.end() :].lstrip()

    return [
        event
        for event in output
        if event["kind"] != "run" or str(event.get("value", "")).strip()
    ]


def _primary_expression_zone(line: dict[str, Any]) -> dict[str, Any]:
    """Consume only the primary bold form, leaving visible aliases in place.

    An earlier parser consumed ``[and alias ...]`` as part of the expression
    zone. That retained the alias as metadata but removed its connector and
    original position from the faithful run stream. This parser consumes the
    first physical bold expression only. Later bold forms remain in
    ``remaining_spans`` so the marker document can reproduce the source line
    exactly.
    """
    spans = line["spans"]
    start = next(
        (
            index
            for index, span in enumerate(spans)
            if span["style"] in {"bold", "bold_italic"}
            and str(span["clean_text"]).strip()
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
    bold_chunks = [str(spans[start]["clean_text"])]
    cursor = start + 1
    while cursor < len(spans):
        next_item = source_parser._next_meaningful_span(spans, cursor)
        if next_item is None:
            break
        next_index, next_span = next_item
        between = "".join(
            str(span["clean_text"]) for span in spans[cursor:next_index]
        )
        if next_span["style"] in {"bold", "bold_italic"}:
            if source_parser.clean_text(between):
                break
            candidate = str(next_span["clean_text"]).strip()
            if source_parser.SENSE_RE.fullmatch(candidate):
                break
            included_end = next_index + 1
            bold_chunks.append(str(next_span["clean_text"]))
            cursor = next_index + 1
            continue

        if next_span["style"] != "roman":
            break
        following = source_parser._next_meaningful_span(spans, next_index + 1)
        if following is None or following[1]["style"] not in {
            "bold",
            "bold_italic",
        }:
            break
        candidate = str(following[1]["clean_text"]).strip()
        if source_parser.SENSE_RE.fullmatch(candidate):
            break
        connector = source_parser.clean_text(
            between + str(next_span["clean_text"])
        )
        structural_roman = bool(
            re.fullmatch(r"[IVXLCDM]+", candidate)
            and source_parser._valid_roman(candidate)
        )
        lexical_joiner = bool(
            connector
            and not re.search(r"\s", connector)
            and re.fullmatch(r"[./'’\-]+", connector)
        )
        if not (structural_roman or lexical_joiner):
            break
        included_end = following[0] + 1
        bold_chunks.append(str(following[1]["clean_text"]))
        cursor = following[0] + 1

    zone_text = "".join(
        str(span["clean_text"]) for span in spans[start:included_end]
    )
    return {
        "start": start,
        "end": included_end,
        "text": source_parser.clean_text(zone_text),
        "bold_chunks": [
            source_parser.clean_text(chunk)
            for chunk in bold_chunks
            if source_parser.clean_text(chunk)
        ],
        "remaining_spans": spans[included_end:],
        "warnings": [],
    }


def _header_connector(text: str) -> bool:
    """Return whether roman header text can connect observed bold forms."""
    cleaned = source_parser.clean_text(text)
    if not cleaned:
        return True
    if source_parser._connector_allows_bold(cleaned):
        return True
    if re.search(r"(?:^|[\s\[])and(?:/or)?\s*$", cleaned, flags=re.I):
        return True
    if re.search(r"(?:^|[\s\[])or\s*$", cleaned, flags=re.I):
        return True
    return bool(re.fullmatch(r"/[^/]+/", cleaned))


def _observed_header_aliases(
    line: dict[str, Any],
    zone: dict[str, Any],
    tag_map: dict[str, dict[str, str]],
    primary_expression: str,
) -> list[str]:
    """Collect later bold forms before definition prose begins.

    The returned values are lookup aliases, not hierarchy. The original spans
    remain in the content stream and therefore retain commas, ``and``/``or``,
    brackets, labels, and typography for display.
    """
    aliases: list[str] = []
    definition_started = False
    for span in line["spans"][int(zone["end"]) :]:
        text = str(span["clean_text"])
        stripped = text.strip()
        if not stripped:
            continue
        style = str(span["style"])

        if style in {"bold", "bold_italic"}:
            if source_parser.SENSE_RE.fullmatch(stripped):
                break
            if definition_started or stripped in {"–", "~"}:
                continue
            alias_parse = source_parser.parse_expression_zone(
                {
                    "text": source_parser.clean_text(text),
                    "bold_chunks": [source_parser.clean_text(text)],
                    "warnings": [],
                }
            )
            alias = source_parser.clean_text(
                str(alias_parse.get("expression", ""))
            )
            if alias and alias != primary_expression and alias not in aliases:
                aliases.append(alias)
            continue

        if style in {"italic", "bold_italic"}:
            # Labels and pronunciation/source qualifiers may precede another
            # observed bold form. They do not by themselves begin prose.
            if source_parser.tag_codes(stripped, tag_map):
                continue
            continue

        if style == "roman":
            if _header_connector(text):
                continue
            definition_started = True
            continue

        if style not in {"symbol", "small_caps"}:
            definition_started = True
    return aliases


def parse_debug_form(
    form: dict[str, Any],
    tag_map: dict[str, dict[str, str]],
    *,
    root_expression: str | None = None,
    line_wrap_evidence: dict[str, set[str]] | None = None,
    line_wrap_resolutions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Parse high-confidence form structure and retain honest body runs."""
    first_line = form["lines"][0]
    zone = _primary_expression_zone(first_line)
    following_text = "".join(
        span["clean_text"] for span in zone["remaining_spans"]
    )
    expression = source_parser.parse_expression_zone(zone, following_text)
    expression["variants"] = list(
        dict.fromkeys(
            [
                *expression["variants"],
                *_observed_header_aliases(
                    first_line,
                    zone,
                    tag_map,
                    str(expression["expression"]),
                ),
            ]
        )
    )
    structural_prefix = ""
    inline_subentry = str(expression.get("inline_subentry") or "")
    if inline_subentry and not any(
        character.isalnum() for character in inline_subentry
    ):
        # Source punctuation can share the headword's bold PDF span. It
        # belongs to the definition header, not a punctuation-only lookup.
        structural_prefix = inline_subentry
        expression["inline_subentry"] = None
    raw_events = _raw_content_events(
        form,
        zone,
        expression,
        tag_map,
        line_wrap_evidence,
        line_wrap_resolutions,
    )
    if structural_prefix:
        raw_events.insert(
            0,
            {
                "kind": "run",
                "style": "roman",
                "value": structural_prefix,
                "boundary": "",
            },
        )
    content = _attach_boundary_operators_to_italics(
        _split_parenthesized_template_operators(
            _coalesce_runs(
                _strip_redundant_sense_delimiters(
                    _demote_reference_suffix_senses(
                        _arrow_cross_references(raw_events)
                    )
                )
            )
        )
    )
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


def _marker_parts(line: str) -> tuple[str, str]:
    match = MARKER_RE.fullmatch(line)
    if match is None:
        raise ValueError(f"Invalid generated marker line: {line!r}")
    return match.group(1), match.group(2) or ""


def _metadata_end(lines: list[str]) -> int:
    index = 1
    while index < len(lines):
        marker, _value = _marker_parts(lines[index])
        if marker not in {"Variant", "Homograph"}:
            break
        index += 1
    return index


def _reviewed_marker_value(marker: str, value: str) -> str:
    if marker in RUN_MARKERS:
        for source, replacement in REVIEWED_RUN_REPAIRS.items():
            value = value.replace(source, replacement)
        value = value.replace("both.. and...", "both... and...")
        value = value.replace("asked.. to", "asked... to")
        value = re.sub(r"(?<!\.)\.\.(?!\.)", ".", value)
        value = re.sub(r"\s+\)", ")", value)
    elif marker == "See":
        for source, replacement in REVIEWED_REFERENCE_REPAIRS.items():
            value = value.replace(source, replacement)
    return value


def _repair_variants(lines: list[str]) -> list[str]:
    _marker, expression = _marker_parts(lines[0])
    repair = REVIEWED_VARIANT_REPAIRS.get(expression)
    if (
        expression == "engah"
        and "[Homograph] II" in lines
        and "[Variant] engah-engah" not in lines
    ):
        insertion = 1
        lines = [
            *lines[:insertion],
            "[Variant] engah-engah",
            *lines[insertion:],
        ]
    if repair is None:
        return [
            line
            for line in lines
            if not (
                _marker_parts(line)[0] == "Variant"
                and _marker_parts(line)[1] in FALSE_LOOKUP_VARIANTS
            )
        ]

    replaced, replacements = repair
    existing_variants = {
        value
        for line in lines[1:]
        for marker, value in [_marker_parts(line)]
        if marker == "Variant"
    }
    output = [lines[0]]
    inserted = all(
        replacement in existing_variants for replacement in replacements
    )
    for line in lines[1:]:
        marker, value = _marker_parts(line)
        if marker == "Variant" and value in replaced:
            if not inserted:
                output.extend(
                    marker_line("Variant", replacement)
                    for replacement in replacements
                    if replacement not in existing_variants
                )
                inserted = True
            continue
        if marker == "Variant" and value in FALSE_LOOKUP_VARIANTS:
            continue
        output.append(line)
    if not inserted:
        insertion = _metadata_end(output)
        output[insertion:insertion] = [
            marker_line("Variant", replacement)
            for replacement in replacements
            if replacement not in existing_variants
        ]
    return output


def _merge_fragmented_release_runs(lines: list[str]) -> list[str]:
    _marker, expression = _marker_parts(lines[0])
    replacements: dict[str, tuple[list[str], list[str]]] = {
        "OK": (
            [
                "[Roman] and",
                "[Bold] O",
                "[Roman] .",
                "[Bold] K",
                "[Roman] .",
            ],
            ["[Roman] and", "[Bold] O.K."],
        ),
        "SH": (
            [
                "[Roman] and",
                "[Bold] S",
                "[Roman] .",
                "[Bold] H",
                "[Roman] . (",
            ],
            ["[Roman] and", "[Bold] S.H.", "[Roman] ("],
        ),
        "vip": (
            [
                "[Roman] and",
                "[Bold] V",
                "[Roman] .",
                "[Bold] I",
                "[Roman] .",
                "[Bold] P",
                "[Roman] .",
            ],
            ["[Roman] and", "[Bold] V.I.P."],
        ),
        "PBR": (
            [
                "[Italic] Bintang R",
                "[BoldItalic] é",
                "[Italic] formasi",
                "[Bold] .",
            ],
            ["[Italic] Bintang Réformasi", "[Roman] ."],
        ),
    }
    repair = replacements.get(expression)
    if repair is None:
        return lines

    source, replacement = repair
    for index in range(len(lines) - len(source) + 1):
        if lines[index : index + len(source)] == source:
            return [
                *lines[:index],
                *replacement,
                *lines[index + len(source) :],
            ]
    return lines


def _apply_reviewed_marker_sequence_repairs(lines: list[str]) -> list[str]:
    """Join reviewed compounds split only by source typography."""
    output = list(lines)
    for source, replacement in REVIEWED_MARKER_SEQUENCE_REPAIRS:
        width = len(source)
        index = 0
        while index <= len(output) - width:
            if tuple(output[index : index + width]) != source:
                index += 1
                continue
            output[index : index + width] = replacement
            index += len(replacement)
    return output


def _promote_embedded_senses(lines: list[str]) -> list[str]:
    """Promote source numbers fused into typographic runs.

    Promotion requires the next explicit source sense to be exactly the
    successor. This prevents ordinary years, measurements, and example
    numbers from becoming dictionary structure.
    """
    output: list[str] = []
    for index, line in enumerate(lines):
        marker, value = _marker_parts(line)
        candidates: list[tuple[str, Any, int]] = []
        if marker in {"Bold", "BoldItalic"}:
            trailing = re.fullmatch(r"(.+?[\w)])\s+(\d{1,2})", value)
            if trailing is not None:
                candidates.append(
                    ("trailing", trailing.group(1), int(trailing.group(2)))
                )
        if marker in {"Bold", "BoldItalic", "Roman"}:
            leading = re.fullmatch(r"([^\w]*)(\d{1,2})\s+(.+)", value)
            if leading is not None:
                candidates.append(
                    (
                        "leading",
                        (leading.group(1), leading.group(3)),
                        int(leading.group(2)),
                    )
                )

        promoted = False
        for position, remainder, number in candidates:
            following_number = next(
                (
                    int(next_value)
                    for next_line in lines[index + 1 :]
                    for next_marker, next_value in [_marker_parts(next_line)]
                    if next_marker == "Sense"
                ),
                None,
            )
            if following_number != number + 1:
                continue
            if position == "trailing":
                output.extend(
                    [
                        marker_line(marker, remainder),
                        marker_line("Sense", number),
                    ]
                )
            else:
                prefix, suffix = remainder
                if prefix.strip():
                    output.append(marker_line(marker, prefix))
                output.extend(
                    [
                        marker_line("Sense", number),
                        marker_line(marker, suffix),
                    ]
                )
            promoted = True
            break
        if not promoted:
            output.append(line)
    return output


def _normalize_sense_boundaries(lines: list[str]) -> list[str]:
    """Keep root senses structural and inline-form resets typographic.

    The source frequently places a bold inline form inside a root entry and
    restarts numbering for that form. The marker schema has no nested sense
    scope, so treating both sequences as one structural series creates false
    discontinuities. Local numbers remain visible as bold source text; a
    later nonconsecutive successor resumes the enclosing structural series.
    """
    lines = _promote_embedded_senses(lines)
    first_sense = next(
        (
            int(value)
            for line in lines
            for marker, value in [_marker_parts(line)]
            if marker == "Sense"
        ),
        None,
    )
    if first_sense is not None and first_sense > 1:
        lines.insert(_metadata_end(lines), marker_line("Sense", 1))

    output: list[str] = []
    previous: int | None = None
    bold_form_since_sense = False
    local_numbering = False
    last_local_number: int | None = None

    for line in lines:
        marker, value = _marker_parts(line)
        if marker != "Sense":
            if (
                marker in {"Bold", "BoldItalic"}
                and re.search(r"[^\W\d_]", value, flags=re.UNICODE)
            ):
                bold_form_since_sense = True
            output.append(line)
            continue

        number = int(value)
        if previous is None:
            output.append(line)
            previous = number
            bold_form_since_sense = False
            continue

        if local_numbering:
            resumes_parent = (
                number == previous + 1
                and (
                    last_local_number is None
                    or number != last_local_number + 1
                )
            )
            if resumes_parent:
                output.append(line)
                previous = number
                bold_form_since_sense = False
                local_numbering = False
                last_local_number = None
            else:
                output.append(marker_line("Bold", number))
                last_local_number = number
            continue

        if number <= previous and bold_form_since_sense:
            output.append(marker_line("Bold", number))
            local_numbering = True
            last_local_number = number
            bold_form_since_sense = False
            continue
        if number == previous:
            # Repeated source numbers without a new bold form describe the
            # same sense. Keep one structural boundary and merge its text.
            continue
        if number < previous:
            # A bold number embedded in prose (for example "[same as 4]")
            # is not a boundary in the current entry.
            output.append(marker_line("Bold", number))
            continue
        if number > previous + 1:
            number = previous + 1
            line = marker_line("Sense", number)

        output.append(line)
        previous = number
        bold_form_since_sense = False
    return output


def normalize_human_intermediate_text(text: str) -> str:
    """Apply reviewed release repairs to generated marker text."""
    source_lines = [
        raw_line
        for raw_line in text.splitlines()
        if raw_line
    ]
    source_lines = _apply_reviewed_marker_sequence_repairs(source_lines)

    forms: list[list[str]] = []
    current: list[str] = []
    for raw_line in source_lines:
        marker, _value = _marker_parts(raw_line)
        if marker in FORM_MARKERS:
            if current:
                forms.append(current)
            current = [raw_line]
        elif current:
            current.append(raw_line)
        else:
            raise ValueError(f"Content appears before a form: {raw_line!r}")
    if current:
        forms.append(current)

    repaired_forms: list[list[str]] = []
    for original in forms:
        lines = list(original)
        head_marker, expression = _marker_parts(lines[0])
        prefixed_content: list[str] = []

        combined_homograph = (
            re.fullmatch(r"(.+?)\s+([IVXLCDM]+)(\d+)", expression)
            if head_marker == "Entry"
            else None
        )
        if combined_homograph is not None:
            expression = combined_homograph.group(1)
            lines[0] = marker_line(head_marker, expression)
            prefixed_content.extend(
                [
                    marker_line("Homograph", combined_homograph.group(2)),
                    marker_line("Sense", combined_homograph.group(3)),
                ]
            )

        inline_sense = re.fullmatch(r"(.*?)\s*(\d+)\s+(=.+)", expression)
        if inline_sense is not None:
            expression = inline_sense.group(1).strip()
            prefixed_content.extend(
                [
                    marker_line("Sense", inline_sense.group(2)),
                    marker_line("Bold", inline_sense.group(3)),
                ]
            )
            if not expression:
                if not repaired_forms:
                    raise ValueError("Orphaned numeric inline form.")
                repaired_forms[-1].extend([*prefixed_content, *lines[1:]])
                continue
            lines[0] = marker_line(head_marker, expression)

        if expression == "Super" and lines[1:3] == [
            "[Sense] 98",
            "[Sense] 1",
        ]:
            expression = "Super 98"
            lines[0] = marker_line(head_marker, expression)
            del lines[1]

        opening_parenthesis = expression.endswith(" (")
        if opening_parenthesis:
            expression = expression[:-2].rstrip()
            lines[0] = marker_line(head_marker, expression)
            prefixed_content.append("[Roman] (")

        lines = _repair_variants(lines)
        insertion = _metadata_end(lines)
        if prefixed_content:
            lines[insertion:insertion] = prefixed_content

        normalized_values: list[str] = []
        for line in lines:
            marker, value = _marker_parts(line)
            normalized_values.append(
                marker_line(marker, _reviewed_marker_value(marker, value))
            )
        # A small number of reviewed sequences become recognizable only after
        # their individual runs have been repaired (for example a line-wrapped
        # spelling inside a composite subentry). The global pass above handles
        # sequences spanning forms; this pass handles those now-normalized
        # sequences within the current form.
        normalized_values = _apply_reviewed_marker_sequence_repairs(
            normalized_values
        )
        lines = _merge_fragmented_release_runs(normalized_values)

        if expression == "fisiografi":
            source = ["[Label] D", "[Roman] /E)) physiography."]
            replacement = ["[Label] D/E", "[Roman] ) physiography."]
            for index in range(len(lines) - 1):
                if lines[index : index + 2] == source:
                    lines[index : index + 2] = replacement
                    break

        repaired_forms.append(lines)

    repaired_forms = [
        _normalize_sense_boundaries(lines)
        for lines in repaired_forms
    ]
    return "\n\n".join("\n".join(form) for form in repaired_forms) + "\n"


def human_intermediate_text(
    debug_entries: list[dict[str, Any]],
    tag_map: dict[str, dict[str, str]],
    line_wrap_evidence: dict[str, set[str]] | None = None,
    line_wrap_resolutions: dict[str, Any] | None = None,
) -> str:
    """Create the typography-preserving Layer 1 marker document."""
    blocks: list[str] = []
    for debug_entry in debug_entries:
        if debug_entry["entry_type"] != "root" or not debug_entry["forms"]:
            continue
        root = parse_debug_form(
            debug_entry["forms"][0],
            tag_map,
            line_wrap_evidence=line_wrap_evidence,
            line_wrap_resolutions=line_wrap_resolutions,
        )
        if not root["expression"]:
            continue

        lines = [marker_line("Entry", root["expression"])]
        lines.extend(marker_line("Variant", value) for value in root["variants"])
        if root["homograph"]:
            lines.append(marker_line("Homograph", root["homograph"]))
        if root["inline_subentry"]:
            lines.extend(
                [
                    "",
                    marker_line(
                        "InlineSubentry",
                        root["inline_subentry"],
                    ),
                ]
            )
        lines.extend(_content_marker_lines(root["content"]))

        for debug_form in debug_entry["forms"][1:]:
            parsed = parse_debug_form(
                debug_form,
                tag_map,
                root_expression=root["expression"],
                line_wrap_evidence=line_wrap_evidence,
                line_wrap_resolutions=line_wrap_resolutions,
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
    generated = "\n\n".join(blocks).rstrip() + "\n"
    return normalize_human_intermediate_text(generated)


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
    line_wrap_evidence = _line_wrap_word_evidence(debug_entries)
    line_wrap_resolutions = _load_line_wrap_resolutions(
        LINE_WRAP_RESOLUTIONS_PATH
    )
    human_text = human_intermediate_text(
        debug_entries,
        tag_map,
        line_wrap_evidence,
        line_wrap_resolutions,
    )
    line_wrap_audit = _line_wrap_audit(
        debug_entries,
        line_wrap_evidence,
        line_wrap_resolutions,
        profile=profile,
    )
    paths["human"].write_text(human_text, encoding="utf-8")
    paths["line_wrap_audit"].write_text(
        json.dumps(line_wrap_audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
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
            line.startswith(("[Subentry] ", "[InlineSubentry] "))
            for line in human_text.splitlines()
        ),
        "human_inline_subentries": sum(
            line.startswith("[InlineSubentry] ")
            for line in human_text.splitlines()
        ),
        "numbered_senses": sum(
            line.startswith("[Sense] ") for line in human_text.splitlines()
        ),
        "line_wrap_counts": line_wrap_audit["counts"],
        "line_wrap_resolution_csv": str(
            LINE_WRAP_RESOLUTIONS_PATH.resolve()
        ),
        "line_wrap_resolution_csv_sha256": source_parser.sha256_file(
            LINE_WRAP_RESOLUTIONS_PATH
        ),
        "line_wrap_audit_output": str(
            paths["line_wrap_audit"].resolve()
        ),
        "human_output": str(paths["human"].resolve()),
        "debug_output": str(paths["debug"].resolve()),
    }
    paths["manifest"].write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary
