#!/usr/bin/env python3
"""Generates the two synthetic Vanguard Brokerage statement PDFs behind
TestExtractStatementVanguardBrokerage in pkg/ingest:

  test/vanguard-voyager-synthetic-sample.pdf
      the older "Vanguard Voyager Select Services" quarter-to-date
      layout - the "Mutual funds" holdings table carries an
      "Average price per share" / "Total cost" column pair.

  test/vanguard-personal-investor-synthetic-sample.pdf
      the current "Vanguard Personal Investor" monthly transaction
      layout - no such columns, and the settlement fund is relabelled
      "Sweep program".

Every name, amount and account number here is invented, so unlike a
redacted real statement (test/*.pdf is gitignored) these fixtures live
in git and give pkg/ingest/py/institutions/vanguard_brokerage.py
regression coverage that runs for everyone.

The layout is reproduced closely enough to exercise the parser's real
work: each table row is one linearised text line with the security name
wrapping onto the following line, the two different holdings column
counts (6 vs 4), and Completed-transactions rows with "-" placeholders
in the money columns.

Regenerate with:  python3 test/gen-vanguard-brokerage-synthetic-sample.py
"""

import os

PAGE_W, PAGE_H = 612, 792
FONT_SIZE = 8
LINE_H = 12
X = 36


VOYAGER_LINES = [
    "March 31, 2024, quarter-to-date statement",
    "View your statements online at vanguard.com.",
    "Vanguard Voyager Select Services",
    "Assets listed in this statement are held by Vanguard Brokerage Services (VBS).",
    "",
    "Individual brokerage account-00007654 Vanguard Voyager Select Services",
    "Account overview $141,100.00",
    "Total account value as of March 31, 2024",
    "",
    "Balances and holdings for Vanguard Brokerage Account-00007654",
    "Your securities are held in your cash account, unless otherwise noted.",
    "Settlement fund",
    "Price on Balance on Balance on",
    "Name Quantity 03/31/2024 12/31/2023 03/31/2024",
    "VANGUARD FEDERAL MONEY 1,500.0000 $1.00 - $1,500.00",
    "MARKET FUND",
    "$0.00 $1,500.00",
    "Mutual funds",
    "Average price Price on Balance on Balance on",
    "Symbol Name per share Total cost Quantity 03/31/2024 12/31/2023 03/31/2024",
    "VTSAX VANGUARD $95.00 $95,000.00 1,000.0000 $120.00 $110,000.00 $120,000.00",
    "TOTAL STOCK MKT IDX ADMIRAL CL",
    "Est. annual income: $1,800.00; Est. yield: 1.50%",
    "VBTLX VANGUARD $10.50 $21,000.00 2,000.0000 $9.80 $20,500.00 $19,600.00",
    "TOTAL BOND MKT IDX ADMIRAL CL",
    "Est. annual income: $700.00; Est. yield: 3.57%",
    "Total Est. annual income: $2,500.00; Est. yield: 1.79% $130,500.00 $141,100.00",
    "",
    "Account activity for Vanguard Brokerage Account-00007654",
    "This section shows trades that have settled by March 31, 2024.",
    "Income summary",
    "Dividends Interest Tax-exempt interest Short-term capital gains Long-term capital gains",
    "March $508.00 $0.00 $0.00 $0.00 $0.00",
    "Completed transactions",
    "Settlement Trade Commissions",
    "date date Symbol Name Transaction type Account type Quantity Price & fees Amount",
    "03/12 03/12 VTSAX VANGUARD Dividend - - - - $450.00",
    "TOTAL STOCK MKT IDX ADMIRAL CL",
    "03/12 03/12 VTSAX VANGUARD Reinvestment Cash 3.7500 $120.0000 - -450.00",
    "TOTAL STOCK MKT IDX ADMIRAL CL",
    "03/20 03/20 VBTLX VANGUARD Dividend - - - - $58.00",
    "TOTAL BOND MKT IDX ADMIRAL CL",
    "03/20 03/20 VBTLX VANGUARD Reinvestment Cash 5.9180 $9.8000 - -58.00",
    "TOTAL BOND MKT IDX ADMIRAL CL",
    "If you had an adjustment to a dividend or interest payment from a previous month,",
    "the monthly amount shown may be overstated.",
]


PERSONAL_INVESTOR_LINES = [
    "April 30, 2025, monthly transaction statement",
    "View your statements online at vanguard.com.",
    "Vanguard Personal Investor",
    "Assets listed in this statement are held by Vanguard Brokerage Services (VBS).",
    "",
    "Trust brokerage account-XXXX3210 Vanguard Personal Investor",
    "Account overview $145,250.00",
    "Total account value as of April 30, 2025",
    "",
    "Balances and holdings for Vanguard Brokerage Account-XXXX3210",
    "Your securities are held in your cash account, unless otherwise noted.",
    "Sweep program",
    "Price on Balance on Balance on",
    "Name Quantity 04/30/2025 03/31/2025 04/30/2025",
    "VANGUARD FEDERAL MONEY 250.0000 $1.00 - $250.00",
    "MARKET FUND",
    "Total Sweep Balance $0.00 $250.00",
    "Mutual funds",
    "Price on Balance on Balance on",
    "Symbol Name Quantity 04/30/2025 03/31/2025 04/30/2025",
    "VTSAX VANGUARD 800.0000 $125.00 - $100,000.00",
    "TOTAL STOCK MKT IDX ADMIRAL CL",
    "VFIAX VANGUARD 100.0000 $450.00 - $45,000.00",
    "500 INDEX ADMIRAL CL",
    "$0.00 $145,000.00",
    "",
    "Account activity for Vanguard Brokerage Account-XXXX3210",
    "This section shows transactions that have settled by April 30, 2025.",
    "Completed transactions",
    "Settlement Trade Commissions",
    "date date Symbol Name Transaction type Account type Quantity Price & fees Amount",
    "04/07 04/07 VTSAX VANGUARD Transfer Cash 800.0000 - - $0.00",
    "TOTAL STOCK MKT IDX ADMIRAL CL",
    "FROM: XXXX7654-1",
    "04/07 04/07 - MMF JRL FR XXXX7654 Transfer - - - - 100.00",
    "FROM: XXXX7654-1",
    "04/07 04/07 - VANGUARD FEDERAL MONEY Sweep in - - - - -100.00",
    "MARKET FUND",
    "04/30 04/30 - VANGUARD FEDERAL MONEY Dividend - - - - 0.85",
    "MARKET FUND",
    "04/30 04/30 - VANGUARD FEDERAL MONEY Reinvestment - - - - -0.85",
    "MARKET FUND",
    "If you had an adjustment to a dividend or interest payment from a previous month,",
    "the monthly amount shown may be overstated.",
]


def escape(s: str) -> bytes:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)").encode("latin-1", "replace")


def build_pdf(lines, path):
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}

    def add_obj(num: int, body: bytes):
        offsets[num] = len(out)
        out.extend(f"{num} 0 obj\n".encode())
        out.extend(body)
        out.extend(b"\nendobj\n")

    add_obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    add_obj(2, b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>")
    add_obj(3, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    add_obj(
        4,
        (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
            "/Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>" % (PAGE_W, PAGE_H)
        ).encode(),
    )

    stream = bytearray()
    y = PAGE_H - 40
    for line in lines:
        if line:
            stream.extend(f"BT /F1 {FONT_SIZE} Tf 1 0 0 1 {X} {y:.2f} Tm (".encode())
            stream.extend(escape(line))
            stream.extend(b") Tj ET\n")
        y -= LINE_H
    add_obj(5, b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + bytes(stream) + b"endstream")

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
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures")
    for name, lines in (
        ("vanguard-voyager-synthetic-sample.pdf", VOYAGER_LINES),
        ("vanguard-personal-investor-synthetic-sample.pdf", PERSONAL_INVESTOR_LINES),
    ):
        path = os.path.join(here, name)
        build_pdf(lines, path)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
