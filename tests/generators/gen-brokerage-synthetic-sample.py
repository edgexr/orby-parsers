#!/usr/bin/env python3
"""Generates test/brokerage-synthetic-sample.pdf, the fixture behind
TestExtractStatementSampleBrokerageSynthetic in pkg/ingest.

Every name, merchant, amount and account number here is invented, so
unlike a real statement this fixture can live in git and give the
brokerage-statement PDF pipeline (bank_statement.py's combined
KIND_BROKERAGE dispatch, see
pkg/ingest/py/institutions/sample_brokerage.py) regression coverage
that runs for everyone.

Unlike the other test/gen-*-sample.py scripts (which reproduce a real
institution's messy text-extraction quirks - wrapped lines, doubled bold
characters, page breaks mid-table), this fixture deliberately uses a
simple pipe-delimited layout: its job is to prove the multi-table
contract (a single statement populating cash_transactions,
brokerage_transactions, AND brokerage_holdings in one parse() call) works
end to end through the whole Go<->sandboxed-Python pipeline, not to
stress-test PDF text layout parsing.

Regenerate with:  python3 test/gen-brokerage-synthetic-sample.py
"""

import os

PAGE_W, PAGE_H = 612, 792
FONT_SIZE = 9
LINE_H = 13
X = 40

LINES = [
    "SAMPLE BROKERAGE SERVICES",
    "Statement Period 06/01/2026 - 06/30/2026",
    "Account 123456789 (Brokerage)",
    "",
    "HOLDINGS",
    "AAPL | Apple Inc | 10.000 | 210.50 | 2105.00 | 1800.00",
    "VOO | Vanguard S&P 500 ETF | 5.000 | 512.30 | 2561.50 | 2400.00",
    "",
    "ACTIVITY",
    "06/03/2026 | Buy | AAPL | Bought 2 shares of AAPL | 2.000 | 205.00 | -410.00",
    "06/15/2026 | Dividend | VOO | Dividend Vanguard S&P 500 ETF |  |  | 12.45",
    "06/28/2026 | Sell | AAPL | Sold 1 share of AAPL | -1.000 | 215.00 | 215.00",
    "",
    "CASH ACTIVITY",
    "06/15/2026 | Dividend Received VOO | 12.45 | 512.45",
    "06/28/2026 | Sale Proceeds AAPL | 215.00 | 727.45",
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
    y = PAGE_H - 50
    for line in lines:
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
    path = os.path.join(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures"), "brokerage-synthetic-sample.pdf")
    build_pdf(LINES, path)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
