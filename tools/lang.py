# SPDX-FileCopyrightText: 2026 Core Devices LLC
# SPDX-License-Identifier: Apache-2.0

"""Manage catalogs and build universal Pebble language packs."""

import argparse
import subprocess
import sys
from pathlib import Path

from lang_commands import make_lang, pack_all_langs, pack_lang


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    make = subparsers.add_parser("make_lang", help="Initialize or update a catalog")
    make.add_argument("--lang", required=True, help="Locale identifier")
    make.add_argument("--pot", required=True, type=Path, help="Current source catalog")
    for command in ("pack_lang", "pack_all_langs"):
        pack = subparsers.add_parser(command, help="Build universal language packs")
        pack.add_argument("--output", type=Path, default=Path("dist"))
        if command == "pack_lang":
            pack.add_argument("--lang", required=True, help="Locale identifier")
    args = parser.parse_args()
    try:
        if args.command == "make_lang":
            make_lang(args.lang, args.pot)
        elif args.command == "pack_lang":
            pack_lang(args.lang, args.output)
        else:
            pack_all_langs(args.output)
    except (
        OSError,
        ValueError,
        RuntimeError,
        KeyError,
        subprocess.CalledProcessError,
    ) as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    sys.exit(main())
