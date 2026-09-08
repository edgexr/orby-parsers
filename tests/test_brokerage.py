"""Brokerage statement / export parsers, ported from the fixture-based
TestExtract* cases in pkg/ingest/brokerage_test.go. Synthetic fixtures
only - the DB-storage tests (TestInsert*) stay on the Go side.
"""

from conftest import approx


def test_sample_brokerage_pdf(run_statement):
    stmt = run_statement("brokerage-synthetic-sample.pdf")
    assert stmt.institution == "Sample Brokerage Services"
    assert stmt.statement_date == "2026-06-30"

    assert len(stmt.brokerage_holdings) == 2
    h = stmt.brokerage_holdings[0]
    assert h["symbol"] == "AAPL" and approx(h["current_value"], 2105.00)
    assert h["account"] == "123456789" and h["accountType"] == "Brokerage"

    assert len(stmt.brokerage_transactions) == 3
    buy = stmt.brokerage_transactions[0]
    assert buy["symbol"] == "AAPL" and buy["action"] == "Buy" and approx(buy["amount"], -410.00)
    div = stmt.brokerage_transactions[1]
    assert div["action"] == "Dividend" and div.get("quantity") is None

    assert len(stmt.transactions) == 2
    cash = stmt.transactions[0]
    assert cash["description"] == "Dividend Received VOO"
    assert approx(cash["amount"], 12.45) and approx(cash["balance"], 512.45)


def _assert_sample_brokerage_export(stmt):
    assert stmt.institution == "Sample Brokerage Services"
    assert len(stmt.transactions) == 0
    assert len(stmt.brokerage_holdings) == 0
    assert len(stmt.brokerage_transactions) == 3
    buy = stmt.brokerage_transactions[0]
    assert buy["symbol"] == "AAPL" and buy["action"] == "Buy"
    assert approx(buy["amount"], -410.00) and buy["accountType"] == "Brokerage"
    div = stmt.brokerage_transactions[1]
    assert div["action"] == "Dividend" and div.get("quantity") is None


def test_sample_brokerage_csv(run_statement):
    _assert_sample_brokerage_export(run_statement("brokerage-csv-synthetic-sample.csv"))


def test_sample_brokerage_xlsx(run_statement):
    _assert_sample_brokerage_export(run_statement("brokerage-xlsx-synthetic-sample.xlsx"))


def test_vanguard_brokerage_xlsx_synthetic(run_statement):
    stmt = run_statement("vanguard-brokerage-xlsx-synthetic-sample.xlsx")
    assert stmt.institution == "Vanguard"
    assert len(stmt.transactions) == 0 and len(stmt.brokerage_holdings) == 0
    assert len(stmt.brokerage_transactions) == 8

    div = stmt.brokerage_transactions[0]
    assert div["date"] == "2024-01-15" and div["action"] == "Dividend"
    assert div["symbol"] == "VFIAX" and approx(div["amount"], 42.18) and div.get("quantity") is None
    buy = stmt.brokerage_transactions[2]
    assert buy["action"] == "Buy" and approx(buy["amount"], -601.00)
    assert approx(buy["quantity"], 5.0) and approx(buy["commission_and_fees"], 1.0)
    free = stmt.brokerage_transactions[3]
    assert free["symbol"] == "VTIAX" and approx(free["commission_and_fees"], 0.0)
    xfer = stmt.brokerage_transactions[7]
    assert xfer["action"] == "TRANSFER FROM BROKERAGE" and approx(xfer["amount"], 0.0)
    for i, txn in enumerate(stmt.brokerage_transactions):
        assert txn["accountType"] == "Brokerage", i


def test_fidelity_brokerage_positions_csv(run_statement):
    stmt = run_statement("fidelity-brokerage-positions-sample.csv")
    assert stmt.institution == "Fidelity Investments"
    assert len(stmt.transactions) == 0 and len(stmt.brokerage_transactions) == 0
    assert len(stmt.brokerage_holdings) == 20

    first = stmt.brokerage_holdings[0]
    assert first["symbol"] == "BRKB" and not first.get("type")
    assert not first.get("is_cash_equivalent")
    assert approx(first["quantity"], 16.317)
    assert approx(first["current_value"], 57.29)
    assert approx(first["cost_basis_total"], 79.12)

    core = stmt.brokerage_holdings[1]
    assert core["symbol"] == "SPAXX"
    assert core.get("is_cash_equivalent") is True
    assert core.get("quantity") is None and core.get("price") is None
    assert core.get("cost_basis_total") is None
    assert approx(core["current_value"], 93.32)

    for h in stmt.brokerage_holdings:
        acct = h.get("account", "")
        assert "The data" not in acct and "Brokerage services" not in acct and "Date downloaded" not in acct


def _find(stmt, symbol):
    return next(h for h in stmt.brokerage_holdings if h["symbol"] == symbol)


def test_vanguard_voyager_synthetic(run_statement):
    stmt = run_statement("vanguard-voyager-synthetic-sample.pdf")
    assert stmt.institution == "Vanguard"
    assert stmt.statement_date == "2024-03-31"
    assert len(stmt.brokerage_holdings) == 3

    total = 0.0
    for h in stmt.brokerage_holdings:
        assert h["account"] == "7654" and h["accountType"] == "Brokerage"
        if h.get("current_value") is not None:
            total += h["current_value"]
    assert approx(total, 141100.00)

    assert _find(stmt, "VMFXX").get("is_cash_equivalent") is True
    vtsax = _find(stmt, "VTSAX")
    assert vtsax["description"] == "VANGUARD TOTAL STOCK MKT IDX ADMIRAL CL"
    assert approx(vtsax["average_cost_basis"], 95.00) and approx(vtsax["cost_basis_total"], 95000.00)
    assert approx(vtsax["estimated_annual_income"], 1800.00)

    assert len(stmt.brokerage_transactions) == 4
    div = stmt.brokerage_transactions[0]
    assert div["action"] == "Dividend" and div["symbol"] == "VTSAX" and approx(div["amount"], 450.00)
    reinv = stmt.brokerage_transactions[1]
    assert reinv["action"] == "Reinvestment" and approx(reinv["amount"], -450.00)
    assert approx(reinv["quantity"], 3.75)
    assert approx(sum(t["amount"] for t in stmt.brokerage_transactions), 0.00)


def test_vanguard_personal_investor_synthetic(run_statement):
    stmt = run_statement("vanguard-personal-investor-synthetic-sample.pdf")
    assert stmt.institution == "Vanguard" and stmt.statement_date == "2025-04-30"
    assert len(stmt.brokerage_holdings) == 3

    total = 0.0
    for h in stmt.brokerage_holdings:
        assert h["account"] == "3210"
        assert h.get("average_cost_basis") is None and h.get("cost_basis_total") is None
        if h.get("current_value") is not None:
            total += h["current_value"]
    assert approx(total, 145250.00)

    assert len(stmt.brokerage_transactions) == 5
    xfer = stmt.brokerage_transactions[0]
    assert xfer["action"] == "Transfer" and xfer["transaction_type"] == "internal_transfer"
    assert xfer["symbol"] == "VTSAX" and approx(xfer["amount"], 0.00)
    assert approx(sum(t["amount"] for t in stmt.brokerage_transactions), 0.00)
