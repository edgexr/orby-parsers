from conftest import approx


def test_fidelity_401k_brokerage_pdf_synthetic(run_statement):
    stmt = run_statement("fidelity-401k-brokerage-pdf-synthetic-sample.pdf")

    assert stmt.institution == "Fidelity NetBenefits"
    assert stmt.statement_date == "2026-06-30"
    assert len(stmt.transactions) == 0

    assert len(stmt.brokerage_holdings) == 3
    first_holding = stmt.brokerage_holdings[0]
    assert first_holding["account"] == "2468"
    assert first_holding["accountType"] == "401(k)"
    assert first_holding["symbol"] == "SYNTHETIC GROWTH INDEX FUND"
    assert first_holding["description"] == "Synthetic Growth Index Fund"
    assert approx(first_holding["quantity"], 60.0)
    assert approx(first_holding["price"], 101.25)
    assert approx(first_holding["current_value"], 6075.00)

    assert len(stmt.brokerage_transactions) == 4
    first_txn = stmt.brokerage_transactions[0]
    assert first_txn["account"] == "2468"
    assert first_txn["accountType"] == "401(k)"
    assert first_txn["date"] == "2026-06-30"
    assert first_txn["description"] == "Statement-period Exchange In"
    assert first_txn["action"] == "Transfer In"
    assert approx(first_txn["amount"], 500.00)

    out = stmt.brokerage_transactions[1]
    assert out["description"] == "Statement-period Exchange Out"
    assert out["action"] == "Transfer Out"
    assert approx(out["amount"], -250.00)

    div = stmt.brokerage_transactions[3]
    assert div["description"] == "Statement-period Dividends & Interest"
    assert div["action"] == "Dividend/Interest"
    assert approx(div["amount"], 42.50)
