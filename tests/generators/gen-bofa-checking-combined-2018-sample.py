#!/usr/bin/env python3
"""Generates test/bofa-checking-combined-2018-synthetic-sample.pdf, a
wholly invented fixture for the modern (2018+) Bank of America "combined
statement" format that
pkg/ingest/py/institutions/bofa_checking_combined.py parses - two
deposit accounts (checking + savings) bundled under a single "Your
combined statement\nfor <Month D, YYYY> to <Month D, YYYY>" header, each
account then using the same per-account block layout
bofa_checking.py's single-account parser handles ("Account number:
XXXX", "Beginning balance on <Month D, YYYY> $X", "Deposits and other
additions"/"Withdrawals and other subtractions" sections with MM/DD/YY
transaction dates). See bofa_checking_combined.py's docstring for why
this is a materially different layout from the legacy 2012-era combined
statement (gen-bofa-combined-2012-sample.py).

Also reproduces one real-world quirk this format has: each account's
"Account summary" block prints one-line deltas like "Deposits and other
additions 1000.00" before the actual transaction table - text that
happens to also match bofa_checking.py's section-header detection, so
the parser has to tolerate it as a harmless false match rather than
letting it corrupt the real transaction table that follows.

Deliberately NOT added to .gitignore's allow-list (unlike
wells-fargo-synthetic-sample.pdf / chase-synthetic-sample.pdf): this
fixture stays local-only, not checked into git. The regression test that
uses it (TestExtractStatementBofaCheckingCombined2018Synthetic in
pkg/ingest/statement_test.go) skips gracefully if the file isn't
present, same as the tests for the real redacted statements.

Regenerate with:  python3 test/gen-bofa-checking-combined-2018-sample.py
"""

import os

PAGE_W, PAGE_H = 612, 792
FONT_SIZE = 8
LINE_H = 11
X = 40

PERIOD_START, PERIOD_END = "January 9, 2020", "February 5, 2020"

CHECKING_LAST4 = "1111"
CHECKING_BEGIN = 1000.00
# (description, MM/DD/YY, amount) - amount always printed positive; sign
# is implied by which section (Deposits vs Withdrawals) it's listed under.
CHECKING_CREDITS = [
    ("Generic Employer Payroll", "01/15/20", 1000.00),
]
CHECKING_DEBITS = [
    ("Generic Utility Bill Pay", "01/20/20", 200.00),
]

SAVINGS_LAST4 = "2222"
SAVINGS_BEGIN = 5000.00
SAVINGS_CREDITS = [
    ("Interest Earned", "02/05/20", 5.00),
]


def money(value: float) -> str:
    return f"{value:,.2f}"


def build_page():
    items = []
    y = PAGE_H - 50

    def emit(s):
        nonlocal y
        items.append((X, y, s))
        y -= LINE_H

    def blank():
        nonlocal y
        y -= LINE_H

    emit("Bank of America, N.A.")
    emit("Your combined statement")
    emit(f"for {PERIOD_START} to {PERIOD_END}")
    blank()
    emit("Your deposit accounts Account/plan number Ending balance")

    checking_end = CHECKING_BEGIN + sum(a for _, _, a in CHECKING_CREDITS) - sum(a for _, _, a in CHECKING_DEBITS)
    savings_end = SAVINGS_BEGIN + sum(a for _, _, a in SAVINGS_CREDITS)
    emit(f"Generic Checking 0000 0000 {CHECKING_LAST4} ${money(checking_end)}")
    emit(f"Generic Savings 0000 0000 {SAVINGS_LAST4} ${money(savings_end)}")
    blank()

    def account_block(last4, product_name, begin_balance, credits, debits, end_balance):
        emit(f"Account number: 0000 0000 {last4}")
        emit(f"Your {product_name}")
        emit("Preferred Rewards Platinum Honors")
        emit("Account summary")
        emit(f"Beginning balance on {PERIOD_START} ${money(begin_balance)}")
        if credits:
            emit(f"Deposits and other additions {money(sum(a for _, _, a in credits))}")
        if debits:
            emit(f"Withdrawals and other subtractions -{money(sum(a for _, _, a in debits))}")
        emit(f"Ending balance on {PERIOD_END} ${money(end_balance)}")
        if credits:
            emit("Deposits and other additions")
            emit("Date Description Amount")
            for desc, date, amount in credits:
                emit(f"{date} {desc} {money(amount)}")
            emit(f"Total deposits and other additions ${money(sum(a for _, _, a in credits))}")
        if debits:
            emit("Withdrawals and other subtractions")
            emit("Date Description Amount")
            for desc, date, amount in debits:
                emit(f"{date} {desc} {money(amount)}")
            emit(f"Total withdrawals and other subtractions ${money(sum(a for _, _, a in debits))}")
        blank()

    account_block(CHECKING_LAST4, "Generic Checking", CHECKING_BEGIN, CHECKING_CREDITS, CHECKING_DEBITS, checking_end)
    account_block(SAVINGS_LAST4, "Generic Savings", SAVINGS_BEGIN, SAVINGS_CREDITS, [], savings_end)

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
    path = os.path.join(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures"), "bofa-checking-combined-2018-synthetic-sample.pdf")
    build_pdf(pages, path)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
