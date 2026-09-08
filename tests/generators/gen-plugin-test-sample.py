#!/usr/bin/env python3
"""Generates test/plugin-test-sample.pdf, the fixture behind the
external-parser-plugin tests in pkg/ingest/extra_parsers_test.go.

Unlike the bank-format fixtures (e.g. gen-wells-fargo-sample.py), this
one isn't modeling any real statement layout - it exists purely to give
a test-only external parser plugin (dropped into <orbyDir>/ingest/
parsers/ at test time) something with distinctive marker text to
detect() on. A single page of plain text is enough; no column layout or
multi-page handling is exercised here, since that's not what this fixture
tests - see "IO contract for parser modules" in CLAUDE.md for what is
being tested (the --extra-parsers-dir dispatch path and the parse()
result schema enforcement).

Regenerate with:  python3 test/gen-plugin-test-sample.py
"""

import os

PAGE_W, PAGE_H = 612, 792
FONT_SIZE = 10

LINES = [
    "ZZTEST-PLUGIN-MARKER",
    "Statement for Zztest Bank test account",
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
    add_obj(4, (
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
        f"/Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>"
    ).encode())

    stream = bytearray()
    y = PAGE_H - 50
    for line in lines:
        stream.extend(f"BT /F1 {FONT_SIZE} Tf 1 0 0 1 50 {y} Tm (".encode())
        stream.extend(escape(line))
        stream.extend(b") Tj ET\n")
        y -= 14
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
    path = os.path.join(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures"), "plugin-test-sample.pdf")
    build_pdf(LINES, path)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
