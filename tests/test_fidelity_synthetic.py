"""Fidelity brokerage parser, ported from
pkg/ingest/fidelity_synthetic_test.go's TestExtractFidelitySynthetic and
TestExtractFidelitySyntheticYearEnd.

Expected values are not transcribed here - the generators
(tests/generators/gen-fidelity-synthetic-sample.py and
gen-fidelity-year-end-sample.py) emit them alongside the PDFs, so fixture
data and assertions move together.
"""

import json

import pytest

from conftest import FIXTURES, approx

_EXPECT = FIXTURES / "fidelity-synthetic-expectations.json"
_YEAR_END_EXPECT = FIXTURES / "fidelity-synthetic-year-end-expectations.json"


def _statements():
    if not _EXPECT.exists():
        pytest.skip("regenerate with tests/generators/gen-fidelity-synthetic-sample.py")
    return json.loads(_EXPECT.read_text())["statements"]


@pytest.mark.parametrize("want", _statements(), ids=lambda w: w["statementDate"])
def test_extract_fidelity_synthetic(run_statement, want):
    stmt = run_statement(want["file"])
    assert stmt.institution == "Fidelity Investments"
    assert stmt.statement_date == want["statementDate"]

    got = {}
    for h in stmt.brokerage_holdings:
        key = h["symbol"]
        if h.get("position_type"):
            key += "/" + h["position_type"]
        if h.get("current_value") is not None:
            got[key] = got.get(key, 0.0) + h["current_value"]
        assert h["account"] == want["account"] and h["accountType"] == want["accountType"], key

    assert len(got) == len(want["positions"]), (got, want["positions"])
    for key, value in want["positions"].items():
        assert approx(got.get(key, 0.0), value), (key, got.get(key), value)

    bought = sold = income = flows = 0.0
    for tx in stmt.brokerage_transactions:
        action = tx.get("action", "")
        if action == "Buy":
            bought += tx["amount"]
        elif action == "Sell":
            sold += tx["amount"]
        elif action in ("Dividend", "Reinvestment", "Interest"):
            income += tx["amount"]
        elif action in ("Deposit", "Withdrawal"):
            flows += tx["amount"]
        else:
            assert tx.get("transaction_type") == "corporate_action", (action, tx["description"])

    assert approx(bought, want["securitiesBought"])
    assert approx(sold, want["securitiesSold"])
    assert approx(income, want["income"])
    assert approx(flows, want["netFlows"])

    assert len(stmt.transactions) == 0
    closing = sum(
        h["current_value"]
        for h in stmt.brokerage_holdings
        if h.get("is_cash_equivalent") and h.get("current_value") is not None
    )
    assert approx(closing, want["closingCoreBalance"])


def test_extract_fidelity_synthetic_year_end(run_statement):
    if not _YEAR_END_EXPECT.exists():
        pytest.skip("regenerate with tests/generators/gen-fidelity-year-end-sample.py")
    want = json.loads(_YEAR_END_EXPECT.read_text())

    stmt = run_statement(want["file"])
    assert stmt.institution == "Fidelity Investments"
    assert stmt.statement_date == want["statementDate"]
    assert len(stmt.transactions) == 0 and len(stmt.brokerage_transactions) == 0
    assert len(stmt.brokerage_holdings) == len(want["positions"])

    total = core = 0.0
    for h in stmt.brokerage_holdings:
        assert h["symbol"], h.get("description")
        assert h["account"] == want["account"] and h["accountType"] == want["accountType"]
        wanted = want["positions"][h["symbol"]]
        assert h.get("current_value") is not None and approx(h["current_value"], wanted)
        total += h["current_value"]

        want_cost = want["costBasis"].get(h["symbol"])
        if want_cost is None:
            assert h.get("cost_basis_total") is None
        else:
            assert h.get("cost_basis_total") is not None and approx(h["cost_basis_total"], want_cost)

        if h.get("is_cash_equivalent") and h.get("current_value") is not None:
            core += h["current_value"]

    assert approx(total, want["totalHoldings"])
    assert approx(core, want["coreValue"])
