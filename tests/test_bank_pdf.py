"""Bank / credit-card statement PDF parsers, ported from
pkg/ingest/statement_test.go's TestExtractStatement* cases. Every fixture
here is a committed or on-demand-generated synthetic PDF, so this is the
portable regression net that runs everywhere.
"""

from conftest import approx


def _running_balance(txns, opening):
    running = opening
    for i, t in enumerate(txns):
        running += t["amount"]
        assert t["balance"] is not None, f"txn {i} ({t['description']}): balance is nil"
        assert approx(t["balance"], running), (
            f"txn {i} ({t['description']}): balance {t['balance']:.2f}, want {running:.2f}"
        )
    return running


def test_wells_fargo_synthetic(run_statement):
    stmt = run_statement("wells-fargo-synthetic-sample.pdf")
    assert stmt.institution == "Wells Fargo"
    assert stmt.first_account == "7890"
    assert stmt.first_account_type == "Checking"
    assert stmt.statement_date == "2025-03-31"
    assert len(stmt.transactions) == 14

    deposits = sum(t["amount"] for t in stmt.transactions if t["amount"] > 0)
    withdrawals = -sum(t["amount"] for t in stmt.transactions if t["amount"] <= 0)
    assert approx(deposits, 8550.42)
    assert approx(withdrawals, 5296.45)

    closing = _running_balance(stmt.transactions, 5000.00)
    assert approx(closing, 8253.97)

    want = [
        (0, "2025-03-01", "Zelle From Alex Rivera", 1200.00),
        (1, "2025-03-01", "Zelle to Jamie Lee", -85.50),
        (3, "2025-03-03", "Northwind Traders Payables", 2500.00),
        (4, "2025-03-05", "Acme Corp Payroll", 3000.00),
        (5, "2025-03-05", "Acme Corp Reversal", -3000.00),
        (7, "2025-03-08", "Check 101", -250.00),
        (9, "2025-03-12", "Deposited OR Cashed Check 102", -1000.00),
        (10, "2025-03-20", "Instant Pmt From Stp", 1850.00),
        (13, "2025-03-31", "Interest Payment", 0.42),
    ]
    for idx, date, desc, amount in want:
        t = stmt.transactions[idx]
        assert t["date"] == date
        assert t["description"].startswith(desc), (idx, t["description"], desc)
        assert approx(t["amount"], amount), (idx, t["amount"], amount)

    assert "Rent Share For March" in stmt.transactions[0]["description"]
    assert stmt.transactions[0].get("reference") == "Ab12Cd34Ef56"


def test_chase_synthetic(run_statement):
    stmt = run_statement("chase-synthetic-sample.pdf")
    assert stmt.institution == "Chase"
    assert stmt.first_account == "8899"
    assert stmt.first_account_type == "Credit Card"
    assert stmt.statement_date == "2026-01-22"
    assert len(stmt.transactions) == 10

    closing = _running_balance(stmt.transactions, -1200.00)
    assert approx(closing, -827.01)

    want = [
        (0, "2025-12-28", "Payment Thank You Bill Pay Service", 1500.00),
        (1, "2026-01-05", "RIVERSIDE OUTFITTERS REFUND", 45.00),
        (2, "2025-12-24", "COFFEE HOUSE 12", -18.75),
        (3, "2025-12-26", "FAKE AIR", -842.10),
        (4, "2025-12-31", "GROCERY MART #221", -96.43),
        (8, "2026-01-20", "DINER ON MAIN", -41.05),
        (9, "2026-01-21", "EBOOK STORE*4Q7XR", -0.99),
    ]
    for idx, date, desc, amount in want:
        t = stmt.transactions[idx]
        assert t["date"] == date
        assert t["description"].startswith(desc), (idx, t["description"], desc)
        assert approx(t["amount"], amount), (idx, t["amount"], amount)

    assert "ORD LHR" in stmt.transactions[3]["description"]
    foreign = stmt.transactions[6]["description"]
    assert "POUND STERLING" in foreign and "EXCHG RATE" in foreign


def test_bofa_credit_card(run_statement):
    stmt = run_statement("bofa-cc-stmt-sample.pdf")
    assert stmt.institution == "Bank of America"
    assert stmt.first_account == "7777"
    assert stmt.first_account_type == "Credit Card"
    assert stmt.statement_date == "2019-08-05"
    assert len(stmt.transactions) == 10

    closing = _running_balance(stmt.transactions, -1150.50)
    assert approx(closing, -570.49)

    purchase = stmt.transactions[0]
    assert purchase["description"].startswith("ROOM MATE MILAN")
    assert approx(purchase["amount"], -45.16)
    assert purchase.get("reference") == "5049"

    payments = [t for t in stmt.transactions if t["description"].startswith("PAYMENT")]
    assert payments, "expected a PAYMENT transaction"
    assert approx(payments[-1]["amount"], 1150.50)


def test_bofa_combined_statement_2012(run_statement):
    stmt = run_statement("bofa-sample-statement-2012.pdf")
    assert stmt.institution == "Bank of America"
    assert stmt.statement_date == "2012-12-06"
    assert len(stmt.transactions) == 12

    checking = [t for t in stmt.transactions if t["accountType"] == "Checking"]
    savings = [t for t in stmt.transactions if t["accountType"] == "Savings"]
    assert len(checking) == 11
    assert len(savings) == 1
    assert savings[0]["account"] == "1790"
    assert approx(savings[0]["amount"], 1.02)
    assert savings[0]["balance"] is not None and approx(savings[0]["balance"], 124704.89)

    running = _running_balance(checking, 86703.94)
    assert approx(running, 84653.73)
    for t in checking:
        assert t["account"] == "7777"

    hoa = [t for t in checking if t["description"].startswith("Hoa Online Pay")]
    assert hoa and approx(hoa[0]["amount"], -506.95)


def test_bofa_checking_combined_2018(run_statement):
    stmt = run_statement("bofa-sample-statement-2018.pdf")
    assert stmt.institution == "Bank of America"
    assert stmt.statement_date == "2019-02-05"
    assert len(stmt.transactions) == 2

    checking = next(t for t in stmt.transactions if t["accountType"] == "Checking")
    savings = next(t for t in stmt.transactions if t["accountType"] == "Savings")
    assert checking["account"] == "6666"
    assert approx(checking["amount"], 0.23)
    assert approx(checking["balance"], 14883.39)
    assert savings["account"] == "7777"
    assert approx(savings["amount"], 0.65)
    assert approx(savings["balance"], 84749.75)


def test_bofa_combined_2012_synthetic(run_statement):
    stmt = run_statement("bofa-combined-2012-synthetic-sample.pdf")
    assert stmt.institution == "Bank of America"
    assert stmt.statement_date == "2012-12-06"
    assert len(stmt.transactions) == 4

    checking = [t for t in stmt.transactions if t["accountType"] == "Checking"]
    savings = [t for t in stmt.transactions if t["accountType"] == "Savings"]
    assert checking and savings
    for t in checking:
        assert t["account"] == "1111"
    for t in savings:
        assert t["account"] == "2222"
    assert approx(_running_balance(checking, 1000.00), 1801.50)
    assert approx(_running_balance(savings, 5000.00), 5005.00)


def test_bofa_checking_combined_2018_synthetic(run_statement):
    stmt = run_statement("bofa-checking-combined-2018-synthetic-sample.pdf")
    assert stmt.institution == "Bank of America"
    assert stmt.statement_date == "2020-02-05"
    assert len(stmt.transactions) == 3

    checking = [t for t in stmt.transactions if t["accountType"] == "Checking"]
    savings = next(t for t in stmt.transactions if t["accountType"] == "Savings")
    assert len(checking) == 2
    for t in checking:
        assert t["account"] == "1111"
    assert approx(_running_balance(checking, 1000.00), 1800.00)
    assert savings["account"] == "2222"
    assert approx(savings["amount"], 5.00)
    assert approx(savings["balance"], 5005.00)


def test_bofa_savings_legacy_single_account_synthetic(run_statement):
    stmt = run_statement("bofa-savings-legacy-single-account-synthetic-sample.pdf")
    assert stmt.institution == "Bank of America"
    assert stmt.statement_date == "2013-03-31"
    assert len(stmt.transactions) == 0


def test_bofa_savings_legacy_single_account_zero_activity_real(run_statement):
    # Redacted real statement, gitignored -> skips where absent.
    stmt = run_statement("bofa-savings-2012.pdf")
    assert stmt.institution == "Bank of America"
    assert stmt.statement_date == "2012-12-24"
    assert len(stmt.transactions) == 0


def test_bofa_checking_with_check_images(run_statement):
    # ensureVision is not wired here (as in the Go test): only the
    # always-correct-without-OCR parts are asserted.
    stmt = run_statement("bofa-sample-statement.pdf")
    assert stmt.institution == "Bank of America"
    assert stmt.first_account == "9041"
    assert stmt.first_account_type == "Checking"
    assert stmt.statement_date == "2016-05-12"

    want_checks = {
        "CHECK #1409": -65, "CHECK #1439": -314, "CHECK #1440": -307,
        "CHECK #1441": -220, "CHECK #1412": -15, "CHECK #1442": -262,
        "CHECK #1443": -159, "CHECK #1444": -300, "CHECK #1410": -200,
        "CHECK #1445": -190, "CHECK #1446": -30, "CHECK #1447": -450,
        "CHECK #1448": -100,
    }
    got = {
        t["description"]: t["amount"]
        for t in stmt.transactions
        if t["description"].startswith("CHECK #")
    }
    assert len(got) == len(want_checks), got
    for desc, amount in want_checks.items():
        assert desc in got and approx(got[desc], amount), (desc, got.get(desc), amount)


def test_bofa_checking_with_check_images_layout_b(run_statement):
    stmt = run_statement("bofa-stmt-checks-synthetic-sample.pdf")
    assert stmt.institution == "Bank of America"
    assert stmt.first_account_type == "Checking"
    assert stmt.statement_date == "2025-01-23"

    want_checks = {"CHECK #568": -300, "CHECK #514": -96}
    got = {
        t["description"]: t["amount"]
        for t in stmt.transactions
        if t["description"].startswith("CHECK #")
    }
    assert len(got) == len(want_checks), got
    for desc, amount in want_checks.items():
        assert desc in got and approx(got[desc], amount), (desc, got.get(desc), amount)
