"""Layer 1: extract faithful debug data and human-readable marker text from PDF.

The parsing grammar intentionally delegates to the repository's proven
``extract_agent1.py`` implementation. This module owns only named profiles,
output paths, manifests, and the stable Layer 1 workflow.
"""

from __future__ import annotations

import hashlib
import json
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

import extract_agent1 as ground_truth  # noqa: E402


LAYER1_VERSION = "1.0.0"
SMALL_PAGE_SPEC = ground_truth.DEFAULT_PAGES
FULL_PAGE_SPEC = "21-1123"
PROFILE_PAGE_SPECS = {
    "small": SMALL_PAGE_SPEC,
    "full": FULL_PAGE_SPEC,
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


def run_layer1(
    *,
    pdf_path: Path,
    intermediate_dir: Path,
    tag_map_path: Path,
    profile: str,
    page_spec: str | None = None,
) -> dict[str, Any]:
    """Run PDF extraction and write the named Layer 1 profile outputs."""
    if ground_truth.pymupdf is None:
        raise RuntimeError("PyMuPDF is required for Layer 1 extraction.")
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    if not tag_map_path.is_file():
        raise FileNotFoundError(tag_map_path)
    ground_truth_sha256 = normalized_source_sha256(GROUND_TRUTH_PATH)
    if ground_truth_sha256 != EXPECTED_GROUND_TRUTH_SHA256:
        raise RuntimeError(
            "extract_agent1.py changed after Layer 1 was frozen. "
            f"Expected {EXPECTED_GROUND_TRUTH_SHA256}, got {ground_truth_sha256}."
        )

    effective_page_spec = page_spec or PROFILE_PAGE_SPECS[profile]
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    paths = profile_paths(intermediate_dir, profile)

    document = ground_truth.pymupdf.open(pdf_path)
    page_count = document.page_count
    document.close()
    selected_pages = ground_truth.parse_page_spec(effective_page_spec, page_count)
    tag_map = ground_truth.load_tag_map(tag_map_path)

    debug_entries = ground_truth.group_debug_entries(pdf_path, selected_pages)
    human_text = ground_truth.human_intermediate_text(debug_entries, tag_map)
    paths["human"].write_text(human_text, encoding="utf-8")
    ground_truth.write_debug_intermediate(
        paths["debug"],
        pdf_path=pdf_path,
        selected_pages=selected_pages,
        entries=debug_entries,
    )

    human_entries = sum(
        line.startswith("[Entry] ") for line in human_text.splitlines()
    )
    human_forms = human_entries + sum(
        line.startswith("[Subentry] ") for line in human_text.splitlines()
    )
    summary = {
        "layer": 1,
        "layer1_version": LAYER1_VERSION,
        "parser_ground_truth": str(GROUND_TRUTH_PATH.resolve()),
        "parser_ground_truth_sha256": ground_truth_sha256,
        "parser_version": ground_truth.SCRIPT_VERSION,
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
        "pdf_sha256": ground_truth.sha256_file(pdf_path),
        "human_entries": human_entries,
        "human_forms": human_forms,
        "human_output": str(paths["human"].resolve()),
        "debug_output": str(paths["debug"].resolve()),
    }
    paths["manifest"].write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary
