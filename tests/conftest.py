"""Shared test helpers for the standalone parser suite.

Mirrors what Orby's Go side (pkg/ingest) does in production: shell out to
the right dispatcher (bank_statement.py for a PDF, csv_statement.py for a
.csv/.xlsx), then assert on the JSON it prints to stdout. Nothing here
imports the parser modules directly - the dispatchers own the
sys.path / "from institutions import ..." setup.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# So the direct-import unit tests (test_parser_common_helpers.py,
# test_bofa_checking_unit.py) can `import parser_common` /
# `from institutions import ...` exactly as a dispatcher does - the
# dispatcher gets this for free from being the entry-point script.
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def pytest_addoption(parser):
    parser.addoption(
        "--extra-parsers-dir",
        action="store",
        default=None,
        help="Directory of user-dropped parser .py files to load alongside the "
        "bundled ones (mirrors the dispatchers' --extra-parsers-dir). Used when "
        "Orby runs this suite with a custom parser included.",
    )


def _extra_parsers_dir(request) -> str | None:
    return request.config.getoption("--extra-parsers-dir")


@dataclass
class Statement:
    """Normalized view of a dispatcher's JSON output, shaped like the Go
    BankStatement (pkg/ingest/statement.go) the tests were ported from."""

    raw: dict
    institution: str = ""
    statement_date: str = ""
    needs_vision_ocr: bool = False
    transactions: list = field(default_factory=list)          # cash_transactions
    brokerage_transactions: list = field(default_factory=list)
    brokerage_holdings: list = field(default_factory=list)

    @classmethod
    def from_json(cls, obj: dict) -> "Statement":
        tables = obj.get("tables") or {}
        return cls(
            raw=obj,
            institution=obj.get("institution", ""),
            statement_date=obj.get("statementDate", ""),
            needs_vision_ocr=bool(obj.get("needsVisionOcr", False)),
            transactions=list(tables.get("cash_transactions") or []),
            brokerage_transactions=list(tables.get("brokerage_transactions") or []),
            brokerage_holdings=list(tables.get("brokerage_holdings") or []),
        )

    @property
    def first_account(self) -> str:
        return self.transactions[0]["account"] if self.transactions else ""

    @property
    def first_account_type(self) -> str:
        return self.transactions[0]["accountType"] if self.transactions else ""


GENERATORS = Path(__file__).resolve().parent / "generators"

# Synthetic fixtures that are NOT committed to git (wholly invented, but
# regenerated on demand rather than stored - see each generator's
# docstring). Committed synthetic fixtures are not listed here.
_GENERATE_ON_DEMAND = {
    "bofa-checking-combined-2018-synthetic-sample.pdf": "gen-bofa-checking-combined-2018-sample.py",
    "bofa-combined-2012-synthetic-sample.pdf": "gen-bofa-combined-2012-sample.py",
    "bofa-savings-legacy-single-account-synthetic-sample.pdf": "gen-bofa-savings-legacy-single-account-sample.py",
}


@pytest.fixture(scope="session", autouse=True)
def _ensure_generated_fixtures():
    for target, gen in _GENERATE_ON_DEMAND.items():
        if not (FIXTURES / target).exists():
            subprocess.run([sys.executable, str(GENERATORS / gen)], check=True)
    yield


def _fixture_path(name: str) -> Path:
    p = FIXTURES / name
    return p


def _run(script: str, path: Path, extra_args: list[str]) -> dict:
    cmd = [sys.executable, str(SCRIPTS / script), str(path), *extra_args]
    proc = subprocess.run(cmd, cwd=SCRIPTS, capture_output=True, text=True)
    out = proc.stdout.strip()
    if not out:
        raise AssertionError(
            f"{script} produced no stdout (exit {proc.returncode}); stderr:\n{proc.stderr}"
        )
    try:
        obj = json.loads(out.splitlines()[-1])
    except json.JSONDecodeError as e:  # pragma: no cover - diagnostic
        raise AssertionError(f"{script} stdout not JSON: {out!r}\nstderr:\n{proc.stderr}") from e
    if "error" in obj:
        raise AssertionError(f"{script} error: {obj['error']}")
    return obj


@pytest.fixture
def run_statement(request):
    """Returns run(fixture_name, *extra_args) -> Statement, dispatching by
    extension. Skips the test if the fixture isn't present (real redacted
    statements are gitignored)."""

    def run(name: str, *extra_args: str) -> Statement:
        path = _fixture_path(name)
        if not path.exists():
            pytest.skip(f"fixture not present locally: {name}")
        script = "csv_statement.py" if path.suffix.lower() in (".csv", ".xlsx") else "bank_statement.py"
        args = list(extra_args)
        epd = _extra_parsers_dir(request)
        if epd:
            args += ["--extra-parsers-dir", epd]
        obj = _run(script, path, args)
        if not obj.get("detected"):
            raise AssertionError(f"{name}: not detected: {obj.get('reason')}")
        return Statement.from_json(obj)

    return run


@pytest.fixture
def dump_pages(request):
    """Returns dump(fixture_name) -> list[str] of raw pdfplumber page text."""

    def dump(name: str) -> list[str]:
        path = _fixture_path(name)
        if not path.exists():
            pytest.skip(f"fixture not present locally: {name}")
        obj = _run("bank_statement.py", path, ["--dump-text"])
        return obj["pages"]

    return dump


def approx(a: float, b: float, tol: float = 0.005) -> bool:
    return abs(a - b) < tol
