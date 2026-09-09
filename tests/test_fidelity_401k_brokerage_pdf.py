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

    assert len(stmt.brokerage_transactions) == 6
    first_txn = stmt.brokerage_transactions[0]
    assert first_txn["account"] == "2468"
    assert first_txn["accountType"] == "401(k)"
    assert first_txn["date"] == "2026-06-30"
    assert first_txn["description"] == "Exchange In - Synthetic Growth Index"
    assert first_txn["action"] == "Transfer In"
    assert approx(first_txn["amount"], 500.00)

    out = stmt.brokerage_transactions[1]
    assert out["description"] == "Exchange Out - Blue Horizon Bond"
    assert out["action"] == "Transfer Out"
    assert approx(out["amount"], -250.00)

    revenue = stmt.brokerage_transactions[2]
    assert revenue["description"] == "Revenue Credit - Synthetic Growth Index"
    assert revenue["action"] == "Revenue Credit"
    assert approx(revenue["amount"], 10.00)

    div = stmt.brokerage_transactions[5]
    assert div["description"] == "Dividends & Interest - Blue Horizon Bond"
    assert div["action"] == "Dividend/Interest"
    assert approx(div["amount"], 12.50)
