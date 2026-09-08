#!/usr/bin/env python3
"""Generates test/chase-synthetic-sample.pdf, the fixture behind
TestExtractStatementChaseSynthetic in pkg/ingest.

Every name, merchant, amount and account number here is invented, so
unlike a real statement this fixture can live in git and give the Chase
credit card parser regression coverage that runs for everyone.

The statement period deliberately straddles a year boundary
(12/23/25 - 01/22/26). Chase prints transaction dates as MM/DD with no
year, so the parser has to decide from the month which side of the
boundary each row falls on - a path a single-year statement never
exercises.

Also reproduces the layout details the parser has to survive:
  - bold headings that extract with every character doubled
    ("AACCCCOOUUNNTT SSUUMMMMAARRYY"), with single spaces between words
  - the activity table breaking across a page, with the column header
    reprinted and a page footer in between
  - wrapped detail lines that belong to the row above (a flight
    itinerary, a foreign-currency conversion) and a "MM/DD CURRENCY"
    line that looks like a transaction row but carries no amount
  - a sub-dollar purchase, which Chase prints with no leading zero
    (".99" rather than "0.99")
  - an ACCOUNT SUMMARY whose Previous/New Balance the parser reconciles
    against, so a dropped or mis-signed row fails the parse

Regenerate with:  python3 test/gen-chase-sample.py
"""

import os

PAGE_W, PAGE_H = 612, 792
FONT_SIZE = 8
LINE_H = 11

X_DATE = 40
X_DESC = 90
X_AMOUNT_R = 560

ACCOUNT_NUMBER = "XXXX XXXX XXXX 8899"
OPENING, CLOSING = "12/23/25", "01/22/26"
PREVIOUS_BALANCE = 1200.00

# (date, description, [continuation lines], amount). Amounts are signed
# as Chase prints them: payments and credits negative, purchases
# positive.
CREDITS = [
    ("12/28", "Payment Thank You Bill Pay Service", [], -1500.00),
    ("01/05", "RIVERSIDE OUTFITTERS REFUND SPRINGFIELD IL", [], -45.00),
]
PURCHASES = [
    ("12/24", "COFFEE HOUSE 12 SPRINGFIELD IL", [], 18.75),
    ("12/26", "FAKE AIR 0001112223334 FAKEAIR.COM TX",
     ["011526 1 R ORD LHR", "2 R LHR ORD"], 842.10),
    ("12/31", "GROCERY MART #221 SPRINGFIELD IL", [], 96.43),
    ("01/02", "STREAMING SERVICE 800-555-0100 CA", [], 15.99),
    # The "01/08 POUND STERLING" line looks like a transaction row but
    # has no amount, so it must fold into the row above, not start one.
    ("01/08", "FOREIGN SHOP London",
     ["01/08 POUND STERLING", "16.77 X 1.342000000 (EXCHG RATE)"], 22.50),
    ("01/14", "HARDWARE DEPOT 88 SPRINGFIELD IL", [], 134.20),
    ("01/20", "DINER ON MAIN 217-555-0199 IL", [], 41.05),
    # Under a dollar, so Chase prints it as ".99" with no leading zero -
    # see signed() below. A transaction regex that requires a digit
    # before the decimal point silently drops this row, which then shows
    # up only as a reconciliation failure.
    ("01/21", "EBOOK STORE*4Q7XR 800-555-0142 WA", [], 0.99),
]

# Transactions before this index go on page 1, the rest on page 2.
PAGE_BREAK_AT = 5


def money(value: float) -> str:
    return f"{value:,.2f}"


def signed(value: float) -> str:
    """Formats a transaction amount the way Chase prints it in the
    activity table. Sub-dollar amounts lose the leading zero (".99", not
    "0.99"); the ACCOUNT SUMMARY figures, which go through money()
    directly, keep theirs.
    """
    s = money(abs(value))
    if s.startswith("0."):
        s = s[1:]
    return f"-{s}" if value < 0 else s


def bold(s: str) -> str:
    """Chase's bold headings extract with every character doubled but
    single spaces between words - "ACCOUNT SUMMARY" comes out as
    "AACCCCOOUUNNTT SSUUMMMMAARRYY".
    """
    return "".join(c if c == " " else c * 2 for c in s)


def text_width(s: str) -> float:
    return len(s) * FONT_SIZE * 0.55


def all_transactions():
    return CREDITS + PURCHASES


def build_pages():
    txns = all_transactions()
    credits_total = round(sum(a for *_, a in CREDITS), 2)
    purchases_total = round(sum(a for *_, a in PURCHASES), 2)
    new_balance = round(PREVIOUS_BALANCE + credits_total + purchases_total, 2)

    pages = []
    items = []

    def emit(x, y, s):
        items.append((x, y, s))

    def table_header(y):
        emit(X_DATE, y, "Date of")
        y -= LINE_H
        emit(X_DATE, y, "Transaction")
        emit(X_DESC, y, "Merchant Name or Transaction Description")
        emit(X_AMOUNT_R - text_width("$ Amount"), y, "$ Amount")
        return y - LINE_H

    def emit_txn(y, date, desc, conts, amount):
        emit(X_DATE, y, date)
        emit(X_DESC, y, desc)
        a = signed(amount)
        emit(X_AMOUNT_R - text_width(a), y, a)
        y -= LINE_H
        for c in conts:
            emit(X_DESC, y, c)
            y -= LINE_H
        return y

    # --- page 1: summary + start of activity ---
    y = PAGE_H - 50
    emit(X_DATE, y, "Manage your account online: Customer Service:"); y -= LINE_H
    emit(X_DATE, y, "www.chase.com/united 1-800-537-7783"); y -= LINE_H * 2
    emit(X_DATE, y, bold("ACCOUNT SUMMARY")); y -= LINE_H
    emit(X_DATE, y, f"Account Number: {ACCOUNT_NUMBER}"); y -= LINE_H
    emit(X_DATE, y, f"Previous Balance ${money(PREVIOUS_BALANCE)}"); y -= LINE_H
    emit(X_DATE, y, f"Payment, Credits -${money(-credits_total)}"); y -= LINE_H
    emit(X_DATE, y, f"Purchases +${money(purchases_total)}"); y -= LINE_H
    emit(X_DATE, y, "Cash Advances $0.00"); y -= LINE_H
    emit(X_DATE, y, "Fees Charged $0.00"); y -= LINE_H
    emit(X_DATE, y, "Interest Charged $0.00"); y -= LINE_H
    emit(X_DATE, y, f"New Balance ${money(new_balance)}"); y -= LINE_H
    emit(X_DATE, y, f"Opening/Closing Date {OPENING} - {CLOSING}"); y -= LINE_H * 2

    emit(X_DATE, y, bold("ACCOUNT ACTIVITY")); y -= LINE_H
    y = table_header(y)
    emit(X_DATE, y, "PAYMENTS AND OTHER CREDITS"); y -= LINE_H

    emitted = 0
    for date, desc, conts, amount in CREDITS:
        y = emit_txn(y, date, desc, conts, amount)
        emitted += 1
    emit(X_DATE, y, "PURCHASE"); y -= LINE_H
    for date, desc, conts, amount in PURCHASES:
        if emitted >= PAGE_BREAK_AT:
            break
        y = emit_txn(y, date, desc, conts, amount)
        emitted += 1

    y -= LINE_H
    emit(X_DATE, y, "0000001 FIS00000 C 1 Y 1 22 26/01/22 Page 1 of 2 05058 MA MA 60856 20210000040006085601")
    pages.append(items)

    # --- page 2: continued activity + year totals ---
    items = []
    y = PAGE_H - 50
    emit(X_DATE, y, "ACCOUNT ACTIVITY (CONTINUED)"); y -= LINE_H
    y = table_header(y)
    for date, desc, conts, amount in txns[PAGE_BREAK_AT:]:
        y = emit_txn(y, date, desc, conts, amount)

    y -= LINE_H
    emit(X_DATE, y, "2026 Totals Year-to-Date"); y -= LINE_H
    emit(X_DATE, y, "Total fees charged in 2026 $0.00"); y -= LINE_H
    emit(X_DATE, y, "Total interest charged in 2026 $0.00"); y -= LINE_H * 2
    emit(X_DATE, y, bold("INTEREST CHARGES")); y -= LINE_H
    emit(X_DATE, y, "Purchases 19.49%(v)(d) - 0 - - 0 -"); y -= LINE_H * 2
    emit(X_DATE, y, "0000001 FIS00000 C 1 Y 1 22 26/01/22 Page 2 of 2 05058 MA MA 60856 20210000040006085602")
    pages.append(items)
    return pages, new_balance


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
    pages, new_balance = build_pages()
    path = os.path.join(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures"), "chase-synthetic-sample.pdf")
    build_pdf(pages, path)
    txns = all_transactions()
    print(f"wrote {path}")
    print(f"  {len(txns)} transactions, previous {money(PREVIOUS_BALANCE)}, "
          f"new balance {money(new_balance)}")


if __name__ == "__main__":
    main()
