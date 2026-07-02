"""Command-line orchestrator for the dictionary pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from layer1_pdf_extractor import PROFILE_PAGE_SPECS, profile_paths, run_layer1
from layer2_yomitan_dictionary_writer import (
    DEFAULT_MAX_COMPONENT_BYTES,
    build_yomitan,
)


WORK_DIR = Path(__file__).resolve().parent
DEFAULT_PDF = WORK_DIR / "acomprehensive.pdf"
DEFAULT_INTERMEDIATE_DIR = WORK_DIR / "intermediate"
DEFAULT_YOMITAN_DIR = WORK_DIR / "yomitan"
DEFAULT_TAG_MAP = WORK_DIR / "acomprehensive_tags_map.csv"


def print_summary(summary: dict[str, Any]) -> None:
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def add_profile_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILE_PAGE_SPECS),
        default="small",
        help="small uses regression pages; full uses PDF pages 21-1123",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--tag-map", type=Path, default=DEFAULT_TAG_MAP)
    parser.add_argument(
        "--intermediate-dir",
        type=Path,
        default=DEFAULT_INTERMEDIATE_DIR,
    )
    parser.add_argument("--yomitan-dir", type=Path, default=DEFAULT_YOMITAN_DIR)
    subparsers = parser.add_subparsers(dest="command", required=True)

    layer1 = subparsers.add_parser("layer1", help="PDF -> faithful marker text")
    add_profile_arguments(layer1)
    layer1.add_argument(
        "--pages",
        help="custom PDF page specification, e.g. 21-30,70,236",
    )

    layer2 = subparsers.add_parser("layer2", help="marker text -> Yomitan")
    add_profile_arguments(layer2)
    layer2.add_argument(
        "--input",
        type=Path,
        help="marker input; defaults to human_readable_<profile>.txt",
    )
    layer2.add_argument("--name", help="output ZIP basename")
    layer2.add_argument(
        "--max-component-bytes",
        type=int,
        default=DEFAULT_MAX_COMPONENT_BYTES,
        help="strict upper bound for each generated Yomitan JSON component",
    )

    all_command = subparsers.add_parser(
        "all",
        help="run Layer 1 then Layer 2",
    )
    add_profile_arguments(all_command)
    all_command.add_argument("--pages", help="custom PDF page specification")
    all_command.add_argument("--name", help="output ZIP basename")
    all_command.add_argument(
        "--max-component-bytes",
        type=int,
        default=DEFAULT_MAX_COMPONENT_BYTES,
    )
    return parser


def run_layer1_command(args: argparse.Namespace) -> dict[str, Any]:
    return run_layer1(
        pdf_path=args.pdf,
        intermediate_dir=args.intermediate_dir,
        tag_map_path=args.tag_map,
        profile=args.profile,
        page_spec=args.pages,
    )


def run_layer2_command(args: argparse.Namespace) -> dict[str, Any]:
    human_path = getattr(args, "input", None) or profile_paths(
        args.intermediate_dir,
        args.profile,
    )["human"]
    return build_yomitan(
        human_path=human_path,
        output_dir=args.yomitan_dir,
        tag_map_path=args.tag_map,
        dictionary_name=(
            args.name
            or (
                "AComprehensive-rc1"
                if args.profile == "full"
                else f"AComprehensive_{args.profile}"
            )
        ),
        max_component_bytes=args.max_component_bytes,
    )


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "layer1":
        summary = run_layer1_command(args)
    elif args.command == "layer2":
        summary = run_layer2_command(args)
    else:
        layer1_summary = run_layer1_command(args)
        layer2_summary = run_layer2_command(args)
        summary = {
            "profile": args.profile,
            "layer1": layer1_summary,
            "layer2": layer2_summary,
        }
    print_summary(summary)


if __name__ == "__main__":
    main()
