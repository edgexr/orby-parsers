"""Fidelity brokerage parser against redacted REAL statements, ported
from pkg/ingest/brokerage_test.go's TestExtractStatementFidelity* cases.

These fixtures and their expected values live outside git (real balances
and a real partial account number), so this whole module skips unless
tests/fixtures/private-statement-expectations.json and the statement PDFs
it names are present locally. It is a local check against real-world
messiness, not CI coverage - the committed synthetic Fidelity coverage is
in test_fidelity_synthetic.py.
"""

import json
import re

import pytest

from conftest import FIXTURES, approx

PRIVATE = FIXTURES / "private-statement-expectations.json"

MASKED_RUN_RE = re.compile(r"[Xx]{3,}")
ACTIVITY_NOTE_RE = re.compile(
    r"TRADE DATE|(?:Short|Long)-term.*(?:gain|loss):|Wash sale of:|refer to confirm"
)
ABSORBED_TOTAL_RE = re.compile(r"Total |of account holdings")


def _load_private():
    if not PRIVATE.exists():
        pytest.skip(f"{PRIVATE.name} not present locally")
    exp = json.loads(PRIVATE.read_text())
    if not exp.get("account") or not exp.get("statements"):
        pytest.skip(f"{PRIVATE.name} has no account or no statements")
    return exp


def _want_for(date: str) -> dict:
    for tc in _load_private()["statements"]:
        if tc["statementDate"] == date:
            return tc
    raise AssertionError(f"no private expectation for {date}")


def _core_fund_txns(stmt):
    return [t for t in stmt.brokerage_transactions if t.get("transaction_type") == "core_fund_activity"]


def _cash_equiv_value(stmt):
    return sum(
        h["current_value"]
        for h in stmt.brokerage_holdings
        if h.get("is_cash_equivalent") and h.get("current_value") is not None
    )


def _all_descriptions(stmt):
    return (
        [t["description"] for t in stmt.transactions]
        + [t["description"] for t in stmt.brokerage_transactions]
        + [h.get("description", "") for h in stmt.brokerage_holdings]
    )


def test_fidelity_january(run_statement):
    want = _want_for("2026-01-31")
    stmt = run_statement("Statement01312026-redacted.pdf")
    assert stmt.institution == "Fidelity Investments"
    assert stmt.statement_date == "2026-01-31"

    assert len(stmt.brokerage_holdings) == 22
    holdings_value = 0.0
    account = ""
    by_symbol = {}
    for h in stmt.brokerage_holdings:
        if h.get("current_value") is not None:
            holdings_value += h["current_value"]
        assert h.get("account") and h["accountType"] == "Brokerage", h["symbol"]
        account = account or h["account"]
        assert h["account"] == account, (h["symbol"], h["account"], account)
        assert h["symbol"], h.get("description")
        by_symbol[h["symbol"]] = h
    assert approx(holdings_value, want["holdingsTotal"])

    core = by_symbol["SPAXX"]
    assert core["type"] == "Core Account"
    assert core.get("is_cash_equivalent") is True
    assert core.get("cost_basis_total") is None

    avgo = by_symbol["AVGO"]
    assert avgo["description"] == "BROADCOM INC COM" and avgo["position_type"] == "Margin"
    sgov = by_symbol["SGOV"]
    assert sgov["description"] == "ISHARES TR 0-3 MNTH TREASRY" and not sgov.get("position_type")
    assert approx(sgov["quantity"], 10000) and approx(sgov["price"], 100.67)
    assert approx(sgov["current_value"], 1006700.00)

    assert len(stmt.brokerage_transactions) == 29
    bought = sold = income = flows = 0.0
    for tx in stmt.brokerage_transactions:
        action = tx.get("action", "")
        if action == "Buy":
            bought += tx["amount"]
        elif action == "Sell":
            sold += tx["amount"]
        elif action in ("Dividend", "Reinvestment"):
            income += tx["amount"]
        elif action in ("Deposit", "Withdrawal"):
            flows += tx["amount"]
            continue
        else:
            assert tx.get("transaction_type") == "corporate_action", (action, tx["description"])
        assert tx.get("security_id") and tx.get("security_id_type") == "CUSIP", tx["description"]

    assert approx(bought, -2636685.48)
    assert approx(sold, 73818.65)
    assert approx(income, 1850.06)
    assert approx(flows, 88804.46)

    buy = stmt.brokerage_transactions[0]
    assert buy["date"] == "2026-01-05" and buy["symbol"] == "SGOV"
    assert buy["security_id"] == "46436E718" and approx(buy["amount"], -1004100.00)
    assert buy["description"] == "You Bought ISHARES TR 0-3 MNTH TREASRY"

    sold_out = [t for t in stmt.brokerage_transactions if t.get("security_id") == "670928100"]
    assert len(sold_out) == 2
    for tx in sold_out:
        assert tx["action"] == "Sell" and not tx.get("symbol")
        assert tx.get("quantity") is not None and tx["quantity"] < 0

    assert len(stmt.transactions) == 0
    assert len(_core_fund_txns(stmt)) == 0
    assert approx(core["current_value"], 352889.36)


def test_fidelity_second_period(run_statement):
    want = _want_for("2026-02-28")
    exp = _load_private()
    stmt = run_statement("Statement02282026-redacted.pdf")
    assert stmt.institution == "Fidelity Investments"
    assert stmt.statement_date == "2026-02-28"
    assert stmt.brokerage_holdings[0]["account"] == exp["account"]

    assert len(stmt.brokerage_holdings) == 18
    holdings_value = 0.0
    for h in stmt.brokerage_holdings:
        if h.get("current_value") is not None:
            holdings_value += h["current_value"]
        assert h["symbol"], h.get("description")
        assert not MASKED_RUN_RE.search(h.get("description", "")), h["description"]
    assert approx(holdings_value, want["holdingsTotal"])

    bought = sold = income = flows = 0.0
    interest = []
    for tx in stmt.brokerage_transactions:
        action = tx.get("action", "")
        if action == "Buy":
            bought += tx["amount"]
        elif action == "Sell":
            sold += tx["amount"]
        elif action in ("Dividend", "Reinvestment"):
            income += tx["amount"]
        elif action == "Interest":
            income += tx["amount"]
            interest.append(tx)
        elif action in ("Deposit", "Withdrawal"):
            flows += tx["amount"]
        else:
            assert tx.get("transaction_type") == "corporate_action", (action, tx["description"])

    assert approx(bought, -99130.00)
    assert approx(sold, 25484.25)
    assert approx(income, 1049.13)
    assert len(interest) == 1 and approx(interest[0]["amount"], 2.62)
    assert approx(flows, 175524.51)

    assert len(stmt.transactions) == 0
    assert len(_core_fund_txns(stmt)) == 0
    assert approx(_cash_equiv_value(stmt), 455817.25)


def test_fidelity_options(run_statement):
    want = _want_for("2026-07-31")
    stmt = run_statement("Statement07312026-redacted.pdf")
    assert stmt.statement_date == "2026-07-31"
    assert len(stmt.brokerage_holdings) == 26

    holdings_value = options_value = 0.0
    options = {}
    for h in stmt.brokerage_holdings:
        if h.get("current_value") is not None:
            holdings_value += h["current_value"]
        if h.get("type") == "Options":
            options[h["symbol"]] = h
            if h.get("current_value") is not None:
                options_value += h["current_value"]
        assert not MASKED_RUN_RE.search(h.get("description", "")), h["description"]
    assert approx(holdings_value, want["holdingsTotal"])
    assert len(options) == 5 and approx(options_value, -20700.00)

    call = options["AVGO260918C420"]
    assert approx(call["quantity"], -2) and approx(call["current_value"], -3900.00)
    assert call["position_type"] == "Margin"
    for h in stmt.brokerage_holdings:
        if h["symbol"] == "AVGO":
            assert h.get("type") == "Stocks"

    bought = sold = income = fees = flows = 0.0
    fee_rows = 0
    specific_share = None
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
        if tx.get("commission_and_fees") is not None:
            fee_rows += 1
            fees += tx["commission_and_fees"]
            assert tx["commission_and_fees"] >= 0, tx["description"]
        if tx.get("security_id") == "8762009CW" and action == "Buy":
            specific_share = tx

    assert approx(bought, -105633.94)
    assert approx(sold, 127864.72)
    assert approx(income, 34.71)
    assert fee_rows == 27 and approx(fees, 35.22)
    assert approx(flows, -17000.00)

    assert specific_share is not None
    assert specific_share["date"] == "2026-07-27" and approx(specific_share["amount"], -178.66)
    assert specific_share["description"] == (
        "You Bought CALL (MU) MICRON TECHNOLOGY JUL 24 26 $960 (100 SHS) CLOSING TRANSACTION"
    )

    assert len(stmt.transactions) == 0
    assert len(_core_fund_txns(stmt)) == 0
    for tx in stmt.brokerage_transactions:
        assert tx["date"] <= "2026-07-31", tx["description"]


@pytest.mark.parametrize(
    "tc", _load_private().get("statements", []) if PRIVATE.exists() else [],
    ids=lambda tc: tc["statementDate"],
)
def test_fidelity_all_periods(run_statement, tc):
    stmt = run_statement(tc["file"])
    assert stmt.institution == "Fidelity Investments"
    assert stmt.statement_date == tc["statementDate"]
    assert len(stmt.brokerage_holdings) == tc["holdings"]
    assert len(stmt.brokerage_transactions) == tc["brokerageTxns"]
    assert len(stmt.transactions) == 0

    corporate = 0
    for tx in stmt.brokerage_transactions:
        if tx.get("transaction_type") != "corporate_action":
            continue
        corporate += 1
        assert tx["amount"] == 0
        assert tx.get("quantity") is not None
    assert corporate == tc["corporateActions"]

    for h in stmt.brokerage_holdings:
        assert h["accountType"] == "Brokerage", h["symbol"]
        assert not h.get("provider_account_id")
        assert h["symbol"], h.get("description")

    net_flows = sum(
        t["amount"] for t in stmt.brokerage_transactions if t.get("action") in ("Deposit", "Withdrawal")
    )
    assert approx(net_flows, tc["netFlows"])

    holdings_total = sum(
        h["current_value"] for h in stmt.brokerage_holdings if h.get("current_value") is not None
    )
    assert approx(holdings_total, tc["holdingsTotal"])

    for d in _all_descriptions(stmt):
        assert not MASKED_RUN_RE.search(d), d
        assert not ACTIVITY_NOTE_RE.search(d), d
        assert not ABSORBED_TOTAL_RE.search(d), d

    assert len(_core_fund_txns(stmt)) == 0


def test_fidelity_zero_core_balance(run_statement):
    stmt = run_statement("Statement04302026-redacted.pdf")
    assert len(_core_fund_txns(stmt)) == 0
    assert approx(_cash_equiv_value(stmt), 32482.82)


def test_fidelity_cash_exchanges_and_core_sweep(run_statement):
    outgoing = run_statement("fid-stmt3.pdf")
    incoming = run_statement("fid-stmt4.pdf")

    assert len(outgoing.transactions) == 0 and len(incoming.transactions) == 0
    assert len(_core_fund_txns(outgoing)) == 0 and len(_core_fund_txns(incoming)) == 0

    interest = [
        t for t in outgoing.brokerage_transactions
        if t.get("action") == "Interest" and approx(t["amount"], 893.56)
    ]
    exchange_out = [
        t for t in outgoing.brokerage_transactions
        if t.get("transaction_type") == "internal_transfer" and t.get("action") == "Transfer Out"
        and t.get("symbol") == "CASH" and approx(t["amount"], -578443.00)
    ]
    exchange_in = [
        t for t in incoming.brokerage_transactions
        if t.get("transaction_type") == "internal_transfer" and t.get("action") == "Transfer In"
        and t.get("symbol") == "CASH" and approx(t["amount"], 578443.00)
    ]
    assert len(interest) == 1
    assert len(exchange_out) == 1 and len(exchange_in) == 1
