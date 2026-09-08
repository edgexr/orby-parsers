#!/usr/bin/env python3
"""Generates test/bofa-savings-legacy-single-account-synthetic-sample.pdf,
a wholly invented fixture reproducing two bugs found together in a real
single-account legacy (circa-2012) Bank of America savings statement -
see pkg/ingest/py/institutions/bofa_combined_statement.py:

  1. The front-page address column (its zip code, itself containing
     digits) lands text between the "Statement Period" label and the
     MM-DD-YY date range in the extracted text - e.g. "Statement
     Period\\nTampa, FL 33622-5118 12-12-12 through 12-24-12" - which
     broke detect()/_statement_period()'s old assumption that only
     non-digit characters separate the label from the date range.

  2. This account's product name ("Generic Money Market Sav") only
     appears in a "Deposit Accounts\\n<name>\\n..." block *before* the
     "Account Number" line (outside the per-account block
     _in_block_product_name searches), and - since this statement has
     zero activity this period (no Additions/Subtractions section at
     all) and no front-page summary table (single-account statements
     don't print one) - neither existing fallback could find it.

Deliberately NOT added to .gitignore's allow-list (unlike
wells-fargo-synthetic-sample.pdf / chase-synthetic-sample.pdf): this
fixture stays local-only, not checked into git. The regression test that
uses it (TestExtractStatementBofaSavingsLegacySingleAccountSynthetic in
pkg/ingest/statement_test.go) skips gracefully if the file isn't
present, same as the tests for the real redacted statements.

Regenerate with:  python3 test/gen-bofa-savings-legacy-single-account-sample.py
"""

import os

PAGE_W, PAGE_H = 612, 792
FONT_SIZE = 8
LINE_H = 11
X = 40

PERIOD_START, PERIOD_END = "03-01-13", "03-31-13"
ACCOUNT_LAST4 = "9999"
PRODUCT_NAME = "Generic Money Market Sav"
BALANCE = 0.00  # zero activity this period - beginning == ending


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

    # Front matter - reproduces the two-column merge that lands zip-code
    # digits between "Statement Period" and the actual date range.
    emit("Bank of America, N.A. Page 1 of 1")
    emit("P.O. Box 25118 Statement Period")
    emit(f"Tampa, FL 33622-5118 {PERIOD_START} through {PERIOD_END}")
    emit("Number of checks enclosed: 0")
    # Front-matter "Account Number:" mention WITH a colon - must NOT be
    # mistaken for the real per-account block start line below (which has
    # no colon).
    emit(f"Account Number: 0000 0000 {ACCOUNT_LAST4}")
    emit("Platinum Privileges")
    emit("XXXXXXXX X XXXXXXXX")
    blank()
    emit("Customer Service Information")
    emit("www.bankofamerica.com")
    blank()

    # Per-account front matter - the product name sits here, several
    # lines before the real "Account Number ..." block-start line, with
    # no "<Product> Additions"/"Subtractions" header anywhere in this
    # statement (zero activity) and no front-page summary table either
    # (single-account statement) - so this is the *only* place a parser
    # can find the product name.
    emit("Deposit Accounts")
    emit(PRODUCT_NAME)
    emit("Platinum Privileges Relationship Account")
    emit("XXXXXXXX X XXXXXXXX XXXX XXX")
    emit("Your Account at a Glance")
    emit(f"Account Number XXXX XXXX {ACCOUNT_LAST4}")
    emit(f"Beginning Balance on {PERIOD_START} $ {money(BALANCE)}")
    emit(f"Ending Balance on {PERIOD_END} $ {money(BALANCE)}")
    emit("Daily Balance Summary")
    emit("Date Balance($)")
    emit(f"Beginning {money(BALANCE)}")

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
    path = os.path.join(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures"), "bofa-savings-legacy-single-account-synthetic-sample.pdf")
    build_pdf(pages, path)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
