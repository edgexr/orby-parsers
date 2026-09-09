"""Regression coverage for parser_common.discover_parsers, the mechanism
that builds each dispatcher's _PARSERS list from the files in its parser
package (institutions/ or csv_institutions/) instead of a hand-maintained
list - so adding a parser is just adding a file.

Run:  make test  (or  .venv/bin/python -m pytest tests/test_parser_discovery.py)
"""

from __future__ import annotations

import pkgutil

import csv_institutions
import institutions
import parser_common
from bank_statement import _PARSERS as PDF_PARSERS
from csv_statement import _PARSERS as CSV_PARSERS


def _package_parser_names(package) -> set[str]:
    names = set()
    for info in pkgutil.iter_modules(package.__path__):
        if info.name.startswith("_"):
            continue
        mod = __import__(f"{package.__name__}.{info.name}", fromlist=["_"])
        if hasattr(mod, "detect") and hasattr(mod, "parse"):
            names.add(info.name)
    return names


def test_every_pdf_parser_file_is_registered():
    discovered = {m.__name__.rsplit(".", 1)[-1] for m in PDF_PARSERS}
    assert discovered == _package_parser_names(institutions)


def test_every_csv_parser_file_is_registered():
    discovered = {m.__name__.rsplit(".", 1)[-1] for m in CSV_PARSERS}
    assert discovered == _package_parser_names(csv_institutions)


def test_helper_modules_are_not_registered():
    # institutions/common.py and institutions/check_ocr.py expose neither
    # detect() nor parse() and must stay out of the try-list.
    names = {m.__name__.rsplit(".", 1)[-1] for m in PDF_PARSERS}
    assert "common" not in names
    assert "check_ocr" not in names


def test_discovery_is_sorted_by_priority_then_name():
    def key(m):
        return (getattr(m, "PRIORITY", parser_common.DEFAULT_PRIORITY), m.__name__.rsplit(".", 1)[-1])

    assert PDF_PARSERS == sorted(PDF_PARSERS, key=key)
    assert CSV_PARSERS == sorted(CSV_PARSERS, key=key)


def test_bofa_checking_combined_precedes_bofa_checking():
    # bofa_checking's header regex is a superset of bofa_checking_combined's,
    # so the combined parser must be tried first - enforced via its
    # module-scope PRIORITY, since alphabetical order alone would not.
    names = [m.__name__.rsplit(".", 1)[-1] for m in PDF_PARSERS]
    assert names.index("bofa_checking_combined") < names.index("bofa_checking")
