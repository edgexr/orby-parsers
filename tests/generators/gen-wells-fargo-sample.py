#!/usr/bin/env python3
"""Generates test/wells-fargo-synthetic-sample.pdf, the fixture behind
TestExtractStatementWellsFargoSynthetic in pkg/ingest.

Every name, amount and account number here is invented, so unlike a
redacted real statement this fixture can live in git and give the Wells
Fargo parser regression coverage that runs for everyone.

The point of generating a PDF rather than checking in extracted text is
that Wells Fargo encodes a transaction's direction *only* by which
column its amount sits in - Deposits/Additions vs
Withdrawals/Subtractions - and pdfplumber's extract_text() flattens
those columns to single spaces. A fixture that hard-coded the flattened
text would assume the very thing the parser has to cope with. So amounts
here are placed at real x offsets in the correct column, and the parser
under test sees them the same way it sees a genuine statement.

Layout mirrors a real consumer checking statement: a cover page summary,
a transaction history that breaks across a page (so the repeated column
header and page furniture get exercised), wrapped description lines, a
totals line and a checks-written summary.

Deliberately includes the cases that defeat description-keyword parsing:
  - a payroll deposit and a same-day reversal of the identical amount,
    where the balance arithmetic alone admits either ordering
  - "Deposited OR Cashed Check", which reads like a credit but is a
    check drawn on the account
  - payers with no directional word at all, resolvable only by arithmetic

Regenerate with:  python3 test/gen-wells-fargo-sample.py
"""

import os

PAGE_W, PAGE_H = 612, 792
FONT_SIZE = 8
LINE_H = 11

# Column x offsets. Amount columns are right-aligned on these edges, the
# same arrangement a real statement uses.
X_DATE = 40
X_DESC = 72
X_ADDITIONS_R = 430
X_SUBTRACTIONS_R = 500
X_BALANCE_R = 572

STATEMENT_DATE = "March 31, 2025"
ACCOUNT_NUMBER = "1234567890"
ACCOUNT_NAME = "Wells Fargo Everyday Checking"
OPENING_BALANCE = 5000.00

# (date, [description lines], amount, is_credit). Amounts are magnitudes;
# is_credit decides which column they're printed in, which is the only
# signal the parser gets.
TRANSACTIONS = [
    ("3/1", ["Zelle From Alex Rivera On 02/28 Ref # Ab12Cd34Ef56",
             "Rent Share For March"], 1200.00, True),
    ("3/1", ["Zelle to Jamie Lee On 03/01 Ref # Zz98Yy76Xx54 Dinner"], 85.50, False),
    ("3/1", ["Recurring Transfer to Savings Ref #Tr001Aa Xxxxxx1111"], 100.00, False),
    # No directional keyword anywhere - only the balance column resolves this.
    ("3/3", ["Northwind Traders Payables 4455667788 Fake Payer"], 2500.00, True),
    # Equal amounts on one day: arithmetic permits either ordering, so the
    # payroll/reversal wording is what separates them.
    ("3/5", ["Acme Corp Payroll 250305 111222333444TT9 Doe,Jane"], 3000.00, True),
    ("3/5", ["Acme Corp Reversal 250305 555666777888TT9 Doe,Jane"], 3000.00, False),
    ("3/5", ["Bill Pay City Power On-Line Xxxxxxxxxxxx2222 On 03-05"], 145.75, False),
    ("3/8", ["Check 101"], 250.00, False),
    ("3/12", ["ATM Withdrawal Authorized On 03/12 100 Main St",
              "Springfield IL 0001234 ATM ID 1234A Card 5678"], 200.00, False),
    # Reads like a deposit, is actually a check drawn on the account.
    ("3/12", ["Deposited OR Cashed Check 102"], 1000.00, False),
    ("3/20", ["Instant Pmt From Stp FBO Fake Rentals, LLC-Fake Rentals, LLC",
              "On 03/20 Ref#20250320043000096P1Baaaa00111222333",
              "Rent:Tenant Sample"], 1850.00, True),
    ("3/20", ["Bill Pay Water District On-Line Xxxxxxx3333 On 03-20"], 95.20, False),
    ("3/28", ["Check 103"], 420.00, False),
    ("3/31", ["Interest Payment"], 0.42, True),
]

# Where the transaction history breaks onto a second page (index into
# TRANSACTIONS), so the parser meets a repeated column header and page
# furniture mid-table.
PAGE_BREAK_AT = 10

CHECKS_WRITTEN = [("101", "3/8", 250.00), ("102", "3/12", 1000.00), ("103", "3/28", 420.00)]


def money(value: float) -> str:
    return f"{value:,.2f}"


def text_width(s: str) -> float:
    """Approximate Helvetica width. Only needs to be good enough to keep
    right-aligned amount columns from colliding with each other.
    """
    return len(s) * FONT_SIZE * 0.55


def compute_rows():
    """Returns (rows, daily_end) where rows carry each transaction's
    signed amount, and daily_end maps a transaction index to the ending
    daily balance printed on it (only the last transaction of each day).
    """
    rows = []
    balance = OPENING_BALANCE
    for date, desc, amount, is_credit in TRANSACTIONS:
        balance = round(balance + (amount if is_credit else -amount), 2)
        rows.append({"date": date, "desc": desc, "amount": amount,
                     "credit": is_credit, "balance": balance})
    daily_end = {}
    for i, row in enumerate(rows):
        if i + 1 == len(rows) or rows[i + 1]["date"] != row["date"]:
            daily_end[i] = row["balance"]
    return rows, daily_end


def table_header(emit, y):
    emit(X_DATE, y, "Deposits/")
    emit(X_ADDITIONS_R - 60, y, "Withdrawals/")
    emit(X_BALANCE_R - 40, y, "Ending")
    y -= LINE_H
    emit(X_DATE, y, "Date")
    emit(X_DESC, y, "Description")
    emit(330, y, "Check No.")
    emit(X_ADDITIONS_R - 40, y, "Additions")
    emit(X_SUBTRACTIONS_R - 45, y, "Subtractions")
    emit(X_BALANCE_R - 55, y, "Daily Balance")
    return y - LINE_H


def build_pages():
    rows, daily_end = compute_rows()
    pages = []

    # --- page 1: cover summary + start of the transaction history ---
    items = []
    def emit(x, y, s):
        items.append((x, y, s))

    y = PAGE_H - 50
    emit(X_DATE, y, STATEMENT_DATE); y -= LINE_H
    emit(X_DATE, y, "Page 1 of 2"); y -= LINE_H * 2
    emit(X_DATE, y, ACCOUNT_NAME); y -= LINE_H
    emit(X_DATE, y, "This is your primary checking account"); y -= LINE_H * 2
    emit(X_DATE, y, f"Statement period activity summary Account number: {ACCOUNT_NUMBER} (primary account)")
    y -= LINE_H * 2
    emit(X_DATE, y, f"Balance on 3/1 {money(OPENING_BALANCE)}"); y -= LINE_H
    deposits = round(sum(r["amount"] for r in rows if r["credit"]), 2)
    withdrawals = round(sum(r["amount"] for r in rows if not r["credit"]), 2)
    emit(X_DATE, y, f"Deposits/Additions {money(deposits)}"); y -= LINE_H
    emit(X_DATE, y, f"Withdrawals/Subtractions - {money(withdrawals)}"); y -= LINE_H
    emit(X_DATE, y, f"Balance on 3/31 ${money(rows[-1]['balance'])}"); y -= LINE_H * 2
    emit(X_DATE, y, "Interest summary"); y -= LINE_H
    emit(X_DATE, y, "Interest paid this statement $0.42"); y -= LINE_H
    emit(X_DATE, y, "Average collected balance $7,412.09"); y -= LINE_H * 2
    emit(X_DATE, y, "Transaction history"); y -= LINE_H
    y = table_header(emit, y)

    def emit_row(emit, y, index, row):
        emit(X_DATE, y, row["date"])
        emit(X_DESC, y, row["desc"][0])
        amount = money(row["amount"])
        # The whole point of the fixture: direction lives in the column.
        right = X_ADDITIONS_R if row["credit"] else X_SUBTRACTIONS_R
        emit(right - text_width(amount), y, amount)
        if index in daily_end:
            bal = money(daily_end[index])
            emit(X_BALANCE_R - text_width(bal), y, bal)
        y -= LINE_H
        for cont in row["desc"][1:]:
            emit(X_DESC, y, cont)
            y -= LINE_H
        return y

    for i, row in enumerate(rows[:PAGE_BREAK_AT]):
        y = emit_row(emit, y, i, row)
    pages.append(items)

    # --- page 2: continued history, totals, checks summary ---
    items = []
    y = PAGE_H - 50
    emit(X_DATE, y, STATEMENT_DATE); y -= LINE_H
    emit(X_DATE, y, "Page 2 of 2"); y -= LINE_H * 2
    emit(X_DATE, y, f"=> {ACCOUNT_NAME} ( continued)"); y -= LINE_H * 2
    y = table_header(emit, y)

    for i, row in enumerate(rows[PAGE_BREAK_AT:], start=PAGE_BREAK_AT):
        y = emit_row(emit, y, i, row)

    y -= LINE_H
    emit(X_DATE, y, "Totals")
    dep, wd = f"${money(deposits)}", f"${money(withdrawals)}"
    emit(X_ADDITIONS_R - text_width(dep), y, dep)
    emit(X_SUBTRACTIONS_R - text_width(wd), y, wd)
    y -= LINE_H * 2
    emit(X_DATE, y, "The Ending Daily Balance does not reflect any pending withdrawals or holds on deposited funds.")
    y -= LINE_H * 2
    emit(X_DATE, y, "Summary of checks written ( c hecks listed are also displayed in the preceding Transaction history section)")
    y -= LINE_H
    emit(X_DATE, y, "Number Date $Amount"); y -= LINE_H
    for num, date, amount in CHECKS_WRITTEN:
        emit(X_DATE, y, f"{num} {date} {money(amount)}")
        y -= LINE_H
    pages.append(items)
    return pages


def escape(s: str) -> bytes:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)").encode("latin-1", "replace")


def build_pdf(pages, path):
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}

    def add_obj(num: int, body: bytes):
        offsets[num] = len(out)
        out.extend(f"{num} 0 obj\n".encode())
        out.extend(body)
        out.extend(b"\nendobj\n")

    # 1 catalog, 2 page tree, 3 font, then each page is a pair of objects.
    page_obj_nums = [4 + 2 * i for i in range(len(pages))]
    kids = " ".join(f"{n} 0 R" for n in page_obj_nums)
    add_obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    add_obj(2, f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    add_obj(3, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for i, items in enumerate(pages):
        page_num, content_num = page_obj_nums[i], page_obj_nums[i] + 1
        add_obj(page_num, (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_num} 0 R >>"
        ).encode())
        stream = bytearray()
        for x, y, s in items:
            stream.extend(f"BT /F1 {FONT_SIZE} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm (".encode())
            stream.extend(escape(s))
            stream.extend(b") Tj ET\n")
        add_obj(content_num, b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + bytes(stream) + b"endstream")

    xref_pos = len(out)
    count = max(offsets) + 1
    out.extend(f"xref\n0 {count}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for num in range(1, count):
        out.extend(f"{offsets[num]:010d} 00000 n \n".encode())
    out.extend(f"trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode())

    with open(path, "wb") as f:
        f.write(bytes(out))


def main():
    rows, _ = compute_rows()
    path = os.path.join(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures"), "wells-fargo-synthetic-sample.pdf")
    build_pdf(build_pages(), path)
    deposits = round(sum(r["amount"] for r in rows if r["credit"]), 2)
    withdrawals = round(sum(r["amount"] for r in rows if not r["credit"]), 2)
    print(f"wrote {path}")
    print(f"  {len(rows)} transactions, deposits {money(deposits)}, "
          f"withdrawals {money(withdrawals)}, closing {money(rows[-1]['balance'])}")


if __name__ == "__main__":
    main()
