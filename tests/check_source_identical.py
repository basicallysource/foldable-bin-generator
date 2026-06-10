"""
Guarantee #1: the geometry engine was not changed in the port.

Asserts every file of rev02's vendored package (api/_lib/binflatten) is
byte-for-byte identical to the reference rev01 package, via sha256.

The reference is the rev01 package at the repo root if present, else the
frozen manifest below (recorded 2026-06-09 from rev01 commit state) — so the
check keeps working even after rev01 is removed from the repo.

Run:  python tests/check_source_identical.py        (exit 0 = identical)
"""

from __future__ import annotations

import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REV02 = os.path.dirname(HERE)
VENDORED = os.path.join(REV02, "api", "_lib", "binflatten")
# rev01's package lived at the repo root until rev02 replaced it; once
# absent, the frozen manifest below is the anchor.
REFERENCE = os.path.join(REV02, "binflatten")

# sha256 of rev01's binflatten/*.py, recorded when rev02 was created.
FROZEN_MANIFEST = {
    "__init__.py": "66f07cc696b162bedddd2108a535b29e92ac7e8f379f638c879e5d673009a850",
    "export.py": "6c25aaef55146cda9bc60b4a055d1774b903f3d4397c82a506dafd9c3010cb37",
    "params.py": "e5892159f7490b50d78611cf2530aab38295959138d61c4e874e702f04865523",
    "pipeline.py": "264cf8bb28c6daa862af78ffde5f40224de02d38fc22218cbd84f008f73f65c7",
    "refold.py": "109d0bc145e56c8917ea61e7b20efbad15ba71c6b2cbab1dc1ddcacb92cd33a0",
    "step_io.py": "48a0356f495bee5fbdb2cdd861e78c5888116aa0e06436dfe75451ad13372531",
    "tester.py": "50fc963b611cbb3a8e8e37edc085eccd8d06e4d352c900bc487df1cdb6de7cb4",
    "unfold.py": "4d6f4ee401940dcac0fb830d0852dbbca101cd91b35b7ca1ad2efccab2f1ac35",
}


def sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def main() -> int:
    failures = []
    vendored = {f: sha256(os.path.join(VENDORED, f))
                for f in sorted(os.listdir(VENDORED)) if f.endswith(".py")}

    if os.path.isdir(REFERENCE):
        reference = {f: sha256(os.path.join(REFERENCE, f))
                     for f in sorted(os.listdir(REFERENCE)) if f.endswith(".py")}
        src = f"live rev01 package ({REFERENCE})"
    else:
        reference = FROZEN_MANIFEST
        src = "frozen manifest (rev01 package no longer in repo)"

    if set(vendored) != set(reference):
        failures.append(f"file sets differ: {sorted(set(vendored) ^ set(reference))}")
    for f in sorted(set(vendored) & set(reference)):
        if vendored[f] != reference[f]:
            failures.append(f"{f}: sha256 mismatch")

    print(f"reference: {src}")
    for f in sorted(vendored):
        mark = "OK " if reference.get(f) == vendored[f] else "DIFF"
        print(f"  [{mark}] {f}")
    if failures:
        print("\nFAIL — vendored geometry engine differs from rev01:")
        for x in failures:
            print("  " + x)
        return 1
    print("\nPASS — vendored binflatten is byte-identical to rev01")
    return 0


if __name__ == "__main__":
    sys.exit(main())
