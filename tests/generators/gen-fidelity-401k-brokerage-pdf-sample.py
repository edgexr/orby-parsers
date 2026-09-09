#!/usr/bin/env python3
"""Generate a wholly synthetic Fidelity NetBenefits 401(k) statement.

The parser is line-oriented, so this PDF keeps the same extracted labels,
section names, and table shapes as the real NetBenefits statement while
using invented names, dates, account numbers, and amounts.
"""

from __future__ import annotations

import os

PAGE_W, PAGE_H = 612, 792
FONT_SIZE = 8
LINE_H = 12
X = 36
OUT_NAME = "fidelity-401k-brokerage-pdf-synthetic-sample.pdf"


LINES = [
    "Fidelity NetBenefits",
    "Retirement Savings Statement",
    "Statement Period: 04/01/2026 to 06/30/2026",
    "Account Number: XQ-0000-2468",
    "",
    "Account Activity",
    "Beginning Balance $10,000.00",
    "Exchange In $500.00",
    "Exchange Out -$250.00",
    "Revenue Credit $12.34",
    "Change In Market Value $125.66",
    "Ending Balance $10,388.00",
    "Dividends & Interest $42.50",
    "",
    "Market Value of Your Account",
    "Investment as of 04/01/2026 Investment as of 06/30/2026",
    "Shares/Units Beginning Ending Price as of 04/01/2026 Price as of 06/30/2026 Market Value Beginning Market Value Ending",
    "INDEX FUNDS (PASSIVELY MANAGED)",
    "Synthetic Growth Index Fund 50.000 60.000 $100.00 $101.25 $5,000.00 $6,075.00",
    "Bond",
    "Blue Horizon Bond Pool 20.000 20.000 $120.00 $124.40 $2,400.00 $2,488.00",
    "Income",
    "Stable Value Income Fd 2,500.000 1,825.000 $1.00 $1.00 $2,500.00 $1,825.00",
    "Account Totals $9,900.00 $10,388.00",
    "",
    "Detailed Transaction History",
    "This synthetic statement intentionally prints aggregate statement-period activity only.",
]


def escape(s: str) -> bytes:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)").encode("latin-1", "replace")


def build_pdf(lines: list[str], path: str) -> None:
    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}

    def add_obj(num: int, body: bytes) -> None:
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
    y = PAGE_H - 45
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


def main() -> None:
    out_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(out_dir, OUT_NAME)
    build_pdf(LINES, path)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
