# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

import argparse
from typing import List, Optional

from .gui import run as run_gui


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="calinet-artifacts")
    parser.add_argument(
        "--file",
        type=str,
        help="Path to a *_physio.tsv or *.tsv.gz file to open on startup",
    )

    sub = parser.add_subparsers(dest="command")

    gui_parser = sub.add_parser("gui", help="Launch the artifact annotation GUI")
    gui_parser.add_argument(
        "--file",
        type=str,
        help="Path to a *_physio.tsv or *.tsv.gz file to open on startup",
    )

    args = parser.parse_args(argv)

    file_arg = getattr(args, "file", None)

    if args.command in (None, "gui"):
        run_gui(file=file_arg)
        return

    parser.print_help()