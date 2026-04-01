# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

import logging
import argparse
from typing import List, Optional

from .gui import run as run_gui
from calinet.logger import init_logging


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="calinet-artifacts")

    parser.add_argument(
        "--file",
        type=str,
        help="Path to a *_physio.tsv or *.tsv.gz file to open on startup",
    )

    parser.add_argument(
        "--output-dir",
        type=str,
        help="Directory to save outputs (default: alongside input file)",
    )

    parser.add_argument(
        "--pspm",
        action="store_true",
        help="Always export as PsPM (default: BIDS physioevents)",
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help=(
            "Enable verbose debug-level logging.\n\n"
            "When set, the logger runs at logging.DEBUG instead of logging.INFO."
        ),
    )

    sub = parser.add_subparsers(dest="command")

    gui_parser = sub.add_parser("gui", help="Launch the artifact annotation GUI")
    gui_parser.add_argument("--file", type=str)
    gui_parser.add_argument("--output-dir", type=str)
    gui_parser.add_argument("--pspm", action="store_true")

    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.debug else logging.INFO
    init_logging(level=log_level)
    logger = logging.getLogger("calinet_artifacts.cli")
    logger.info("Starting application")

    file_arg = getattr(args, "file", None)

    if args.command in (None, "gui"):
        run_gui(
            file=file_arg,
            output_dir=args.output_dir,
            force_pspm=args.pspm,
        )
        return

    parser.print_help()
    