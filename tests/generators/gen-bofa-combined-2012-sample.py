#!/usr/bin/env python3
"""Generates test/bofa-combined-2012-synthetic-sample.pdf, a wholly
invented fixture for the legacy (circa-2012) Bank of America "Combined
Statement" format that pkg/ingest/py/institutions/bofa_combined_statement.py
parses - two deposit accounts (a checking account and a savings account),
each with its own account number, beginning/ending balance, and its own
"<Product> Additions"/"<Product> Subtractions" transaction sections.
Dates print as MM-DD-YY in the header period line and MM-DD (no year) on
transaction lines - see that module's docstring for the full format.

Deliberately NOT added to .gitignore's allow-list (unlike
wells-fargo-synthetic-sample.pdf / chase-synthetic-sample.pdf): this
fixture stays local-only, not checked into git. The regression test that
uses it (TestExtractStatementBofaCombined2012Synthetic in
pkg/ingest/statement_test.go) skips gracefully if the file isn't present,
same as the tests for the real redacted statements.

Regenerate with:  python3 test/gen-bofa-combined-2012-sample.py
"""

import os

PAGE_W, PAGE_H = 612, 792
FONT_SIZE = 8
LINE_H = 11
X = 40

PERIOD_START, PERIOD_END = "11-07-12", "12-06-12"

CHECKING_LAST4 = "1111"
CHECKING_BEGIN = 1000.00
# (description, MM-DD, amount) - amount always printed positive; sign is
# implied by which section (Additions vs Subtractions) it's listed under.
CHECKING_CREDITS = [
    ("Generic Employer Payroll", "11-15", 1000.00),
    ("Interest Earned", "12-06", 1.50),
]
CHECKING_DEBITS = [
    ("Generic Utility Bill Pay", "11-20", 200.00),
]

SAVINGS_LAST4 = "2222"
SAVINGS_BEGIN = 5000.00
SAVINGS_CREDITS = [
    ("Interest Earned", "12-06", 5.00),
]


def money(value: float) -> str:
    return f"{value:,.2f}"


def build_page():
    items = []
    y = PAGE_H - 50

    def emit(s, indent=0):
        nonlocal y
        items.append((X + indent, y, s))
        y -= LINE_H

    def blank():
        nonlocal y
        y -= LINE_H

    emit("Bank of America, N.A.")
    emit("Statement Period")
    emit(f"{PERIOD_START} through {PERIOD_END}")
    blank()
    emit("Your Platinum Privileges Statement Summary")
    checking_end = CHECKING_BEGIN + sum(a for _, _, a in CHECKING_CREDITS) - sum(a for _, _, a in CHECKING_DEBITS)
    savings_end = SAVINGS_BEGIN + sum(a for _, _, a in SAVINGS_CREDITS)
    emit("Bank Deposit Accounts")
    emit(f"Generic Checking 0000 0000 {CHECKING_LAST4} 12-06 {money(checking_end)}")
    emit(f"Generic Savings 0000 0000 {SAVINGS_LAST4} 12-06 {money(savings_end)}")
    blank()

    # --- checking account block ---
    emit(f"Account Number 0000 0000 {CHECKING_LAST4}")
    emit(f"Beginning Balance on {PERIOD_START} $ {money(CHECKING_BEGIN)}")
    emit("Generic Checking Additions")
    emit("Deposits and Other Additions Date Posted Amount($)")
    for desc, mmdd, amount in CHECKING_CREDITS:
        emit(f"{desc} {mmdd} {money(amount)}")
    emit(f"Total Deposits and Other Additions ${money(sum(a for _, _, a in CHECKING_CREDITS))}")
    emit("Generic Checking Subtractions")
    emit("Other Subtractions Date Posted Amount($)")
    for desc, mmdd, amount in CHECKING_DEBITS:
        emit(f"{desc} {mmdd} {money(amount)}")
    emit(f"Total Other Subtractions ${money(sum(a for _, _, a in CHECKING_DEBITS))}")
    emit("Daily Balance Summary")
    emit("Date Balance($)")
    blank()

    # --- savings account block ---
    emit(f"Account Number 0000 0000 {SAVINGS_LAST4}")
    emit(f"Beginning Balance on {PERIOD_START} $ {money(SAVINGS_BEGIN)}")
    emit("Generic Savings Additions")
    emit("Deposits and Other Additions Date Posted Amount($)")
    for desc, mmdd, amount in SAVINGS_CREDITS:
        emit(f"{desc} {mmdd} {money(amount)}")
    emit(f"Total Deposits and Other Additions ${money(sum(a for _, _, a in SAVINGS_CREDITS))}")
    emit("Daily Balance Summary")
    emit("Date Balance($)")

    return [items]


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
    pages = build_page()
    path = os.path.join(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures"), "bofa-combined-2012-synthetic-sample.pdf")
    build_pdf(pages, path)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
