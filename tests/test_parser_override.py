"""Regression coverage for the '-'/'_' insensitive parser identity used
when a locally-built parser (dash-named by Orby's Build Transactions
Extractor) and its upstream-merged bundled copy (dash->underscore
normalized) are the same parser:

  - parser_common.parser_name_key normalizes the two spellings together
  - merge_parsers lets the local copy keep overriding the bundled one
  - bundled_shadow_of reports that collision (surfaced as a warning, not
    an error, on the Verify step)
  - check_expected_parser accepts either spelling as "the intended parser"

Run:  make test  (or  .venv/bin/python -m pytest tests/test_parser_override.py)
"""

from __future__ import annotations

import parser_common


class _FakeModule:
    def __init__(self, name, path=None):
        self.__name__ = name
        if path is not None:
            self.__file__ = path

    def detect(self, *_):
        return True, "always matches"

    def parse(self, *_):
        raise AssertionError("not called in these tests")


def test_parser_name_key_is_dash_underscore_and_package_insensitive():
    k = parser_common.parser_name_key
    assert k("fidelity-401k-brokerage-pdf") == k("fidelity_401k_brokerage_pdf")
    assert k("institutions.fidelity_401k_brokerage_pdf") == k("fidelity-401k-brokerage-pdf")
    assert k("foo-bar.py".removesuffix(".py")) == k("foo_bar")


def test_merge_parsers_local_dash_copy_overrides_bundled_underscore():
    bundled = [_FakeModule("institutions.fidelity_401k_brokerage_pdf"), _FakeModule("institutions.bofa_checking")]
    local = _FakeModule("fidelity-401k-brokerage-pdf")
    merged = parser_common.merge_parsers(bundled, [local])
    # same length (replaced in place, not appended) and the local copy sits
    # in the bundled parser's original slot
    assert len(merged) == 2
    assert merged[0] is local
    assert merged[1] is bundled[1]


def test_merge_parsers_unrelated_name_is_appended():
    bundled = [_FakeModule("institutions.bofa_checking")]
    new = _FakeModule("some-new-bank-pdf")
    merged = parser_common.merge_parsers(bundled, [new])
    assert merged == [bundled[0], new]


def test_bundled_shadow_of_reports_dash_underscore_collision():
    bundled = [_FakeModule("institutions.fidelity_401k_brokerage_pdf")]
    local = _FakeModule("fidelity-401k-brokerage-pdf")
    assert parser_common.bundled_shadow_of(bundled, local) == "fidelity_401k_brokerage_pdf.py"


def test_bundled_shadow_of_silent_for_exact_name_override_and_new_parser():
    bundled = [_FakeModule("institutions.bofa_checking")]
    # exact-name override (the documented "fix a bundled parser in place"
    # workflow) is intentionally not reported
    assert parser_common.bundled_shadow_of(bundled, _FakeModule("bofa_checking")) is None
    # a genuinely new parser shadows nothing
    assert parser_common.bundled_shadow_of(bundled, _FakeModule("some-new-bank-pdf")) is None
    assert parser_common.bundled_shadow_of(bundled, None) is None


_BODY = '"""A parser."""\n\n\ndef detect(head):\n    return "acme" in head, "acme"\n\n\ndef parse(pages, path, vision=None):\n    return {}\n'


def test_bundled_shadow_identical_ignores_spdx_and_trailing_whitespace(tmp_path):
    upstream = tmp_path / "fidelity_401k_brokerage_pdf.py"
    upstream.write_text("# SPDX-License-Identifier: Apache-2.0\n" + _BODY)
    local = tmp_path / "fidelity-401k-brokerage-pdf.py"
    local.write_text(_BODY + "\n\n")  # no SPDX, extra trailing newlines

    bundled = [_FakeModule("institutions.fidelity_401k_brokerage_pdf", str(upstream))]
    local_mod = _FakeModule("fidelity-401k-brokerage-pdf", str(local))
    assert parser_common.bundled_shadow_of(bundled, local_mod) == "fidelity_401k_brokerage_pdf.py"
    assert parser_common.bundled_shadow_identical(bundled, local_mod) is True


def test_bundled_shadow_identical_false_when_body_differs(tmp_path):
    upstream = tmp_path / "fidelity_401k_brokerage_pdf.py"
    upstream.write_text("# SPDX-License-Identifier: Apache-2.0\n" + _BODY)
    local = tmp_path / "fidelity-401k-brokerage-pdf.py"
    local.write_text(_BODY.replace('return {}', 'return {"institution": "Fidelity"}'))

    bundled = [_FakeModule("institutions.fidelity_401k_brokerage_pdf", str(upstream))]
    local_mod = _FakeModule("fidelity-401k-brokerage-pdf", str(local))
    assert parser_common.bundled_shadow_identical(bundled, local_mod) is False


def test_bundled_shadow_identical_false_when_no_shadow(tmp_path):
    p = tmp_path / "some-new-bank-pdf.py"
    p.write_text(_BODY)
    assert parser_common.bundled_shadow_identical([], _FakeModule("some-new-bank-pdf", str(p))) is False


def test_check_expected_parser_accepts_either_spelling():
    local = _FakeModule("fidelity-401k-brokerage-pdf")
    bundled = _FakeModule("institutions.fidelity_401k_brokerage_pdf")
    # local copy matched, expected is the dash spelling -> ok
    assert parser_common.check_expected_parser(local, "", "fidelity-401k-brokerage-pdf.py") is None
    # bundled underscore copy matched, expected still dash -> also ok
    assert parser_common.check_expected_parser(bundled, "", "fidelity-401k-brokerage-pdf.py") is None
    # a genuinely different parser matching first is still an error
    msg = parser_common.check_expected_parser(_FakeModule("chase_checking"), "", "fidelity-401k-brokerage-pdf.py")
    assert msg and "matched first instead" in msg
