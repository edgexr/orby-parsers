#!/usr/bin/env python3
"""Generates test/bofa-stmt-checks-synthetic-sample.pdf, a wholly
invented fixture for the "layout B" Bank of America check-images page
format (see pkg/ingest/py/institutions/check_ocr.py's
find_check_image_pages docstring): a literal "Check images" section
header and "Check number: N | Amount: $X.XX" labels as real extractable
text, with each check pictured as its own smaller image (more than one
per page) rather than one image filling the page - as opposed to the
older, blanker full-page-scan layout
gen-bofa-checking-combined-2018-sample.py's sibling fixtures don't cover
either (see TestExtractStatementBofaCheckingWithCheckImages, which uses
a real redacted statement for that layout instead).

This exists because the original regression test for layout B used a
private real statement (test/bofa-stmt-checks.pdf) that must not be
committed or relied on staying present. The two check face images here
are wholly drawn/invented placeholders (_draw_placeholder_check, not a
photo or scan of any real check - test/check_sample.png, a real
photographed check, was deliberately NOT used, to keep any real
person's name/address/account numbers out of git history entirely)
placed at roughly the same page-area fraction real layout-B check
images occupy (see check_ocr._MIN_CHECK_IMAGE_AREA_FRACTION) - close
enough to exercise the image-area-based page detection, even though the
real format this is modeled on may differ in exact proportions/DPI.

Regenerate with:  python3 test/gen-bofa-stmt-checks-sample.py
(needs Pillow - use the ingest venv's python, e.g.
~/.orby/ingest/venv/bin/python3, which already has it as a pikepdf
dependency)
"""

import io
import os

from PIL import Image, ImageDraw

PAGE_W, PAGE_H = 612, 792
FONT_SIZE = 8
LINE_H = 11
X = 40

PERIOD_START, PERIOD_END = "December 21, 2024", "January 23, 2025"
ACCOUNT_LAST4 = "0000"
BEGIN_BALANCE = 100.00
# (date MM/DD/YY, check number, amount - always printed positive, checks
# are always withdrawals so bofa_checking.py negates it).
CHECKS = [
    ("01/06/25", "568", 300.00),
    ("01/21/25", "514", 96.00),
]

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures")


def money(value: float) -> str:
    return f"{value:,.2f}"


def escape(s: str) -> bytes:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)").encode("latin-1", "replace")


def build_statement_page():
    """Page 1: the ordinary text-based statement (header, beginning
    balance, "Checks" summary section) bofa_checking.py's own text
    parsing already covers - see TestExtractStatementBofaCheckingCombined
    2018Synthetic for that same coverage. This fixture's whole point is
    page 2 below, so this page is minimal."""
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
    emit("Your BofA Core Checking")
    emit(f"for {PERIOD_START} to {PERIOD_END}")
    emit(f"Account number: 0000 0000 {ACCOUNT_LAST4}")
    blank()
    emit(f"Beginning balance on {PERIOD_START} ${money(BEGIN_BALANCE)}")
    emit("Checks")
    emit("Date Check # Amount")
    for date, num, amount in CHECKS:
        emit(f"{date} {num} -{money(amount)}")
    emit(f"Total checks -${money(sum(a for _, _, a in CHECKS))}")

    return {"text_items": items, "images": []}


def build_check_images_page(check_jpeg: bytes, check_w: int, check_h: int):
    """Page 2: the "layout B" check-images page - a literal "Check
    images" header and "Check number: N | Amount: $X.XX" labels as real
    text (unlike the older full-page-scan layout, where even the labels
    are rasterized), with each check its own smaller image rather than
    one image filling the page."""
    items = []
    y = PAGE_H - 50

    def emit(s):
        nonlocal y
        items.append((X, y, s))
        y -= LINE_H

    emit(f"JONATHAN DOE | Account # 0000 0000 {ACCOUNT_LAST4} | {PERIOD_START} to {PERIOD_END}")
    emit("Check images")
    emit(f"Account number: 0000 0000 {ACCOUNT_LAST4}")
    emit(
        f"Check number: {CHECKS[0][1]} | Amount: ${money(CHECKS[0][2])}    "
        f"Check number: {CHECKS[1][1]} | Amount: ${money(CHECKS[1][2])}"
    )
    emit("Page 2 of 2")

    # Two check face images side by side, each covering roughly the same
    # page-area fraction (~4%) a real layout-B check image does -
    # comfortably above check_ocr._MIN_CHECK_IMAGE_AREA_FRACTION (0.02).
    disp_w, disp_h = PAGE_W * 0.35, PAGE_H * 0.12
    top_y = PAGE_H - 250
    images = [
        {"name": "/Im0", "jpeg": check_jpeg, "w": check_w, "h": check_h,
         "x": X, "y": top_y - disp_h, "disp_w": disp_w, "disp_h": disp_h},
        {"name": "/Im1", "jpeg": check_jpeg, "w": check_w, "h": check_h,
         "x": X + disp_w + 30, "y": top_y - disp_h, "disp_w": disp_w, "disp_h": disp_h},
    ]
    return {"text_items": items, "images": images}


def build_pdf(pages, path):
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}

    def add_obj(num: int, body: bytes):
        offsets[num] = len(out)
        out.extend(f"{num} 0 obj\n".encode())
        out.extend(body)
        out.extend(b"\nendobj\n")

    def add_stream_obj(num: int, dict_body: str, stream: bytes):
        offsets[num] = len(out)
        out.extend(f"{num} 0 obj\n".encode())
        out.extend(f"<< {dict_body} /Length {len(stream)} >>\nstream\n".encode())
        out.extend(stream)
        out.extend(b"\nendstream\nendobj\n")

    # Object numbering: 1=Catalog, 2=Pages, 3=Font. Then, per page: a
    # page object, a content-stream object, and one image object per
    # embedded image - all allocated up front so cross-references
    # between them (e.g. a page's /Resources listing its image objects)
    # can be written out in a single pass.
    next_num = 4
    page_plan = []
    for p in pages:
        page_num = next_num
        next_num += 1
        content_num = next_num
        next_num += 1
        image_nums = {}
        for im in p["images"]:
            image_nums[im["name"]] = next_num
            next_num += 1
        page_plan.append((page_num, content_num, image_nums))

    kids = " ".join(f"{plan[0]} 0 R" for plan in page_plan)
    add_obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    add_obj(2, f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    add_obj(3, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for p, (page_num, content_num, image_nums) in zip(pages, page_plan):
        xobj_entries = " ".join(f"{name} {num} 0 R" for name, num in image_nums.items())
        resources = f"/Font << /F1 3 0 R >>"
        if xobj_entries:
            resources += f" /XObject << {xobj_entries} >>"
        add_obj(page_num, (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
            f"/Resources << {resources} >> /Contents {content_num} 0 R >>"
        ).encode())

        stream = bytearray()
        for x, y, s in p["text_items"]:
            stream.extend(f"BT /F1 {FONT_SIZE} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm (".encode())
            stream.extend(escape(s))
            stream.extend(b") Tj ET\n")
        for im in p["images"]:
            stream.extend(
                f"q {im['disp_w']:.2f} 0 0 {im['disp_h']:.2f} {im['x']:.2f} {im['y']:.2f} cm {im['name']} Do Q\n".encode()
            )
        add_obj(content_num, f"<< /Length {len(stream)} >>\nstream\n".encode() + bytes(stream) + b"endstream")

        for im in p["images"]:
            num = image_nums[im["name"]]
            add_stream_obj(
                num,
                f"/Type /XObject /Subtype /Image /Width {im['w']} /Height {im['h']} "
                f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode",
                im["jpeg"],
            )

    xref_pos = len(out)
    count = max(offsets) + 1
    out.extend(f"xref\n0 {count}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for num in range(1, count):
        out.extend(f"{offsets[num]:010d} 00000 n \n".encode())
    out.extend(f"trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode())

    with open(path, "wb") as f:
        f.write(bytes(out))


def _draw_placeholder_check() -> Image.Image:
    """A wholly invented check-looking image - rectangle border, a fake
    bank name, fake payee/amount/memo lines, fake MICR-looking digits -
    not a photo or scan of anything real. Only its rough layout (text
    concentrated in the usual places) and pixel size matter for this
    fixture; find_check_image_pages doesn't look inside the image at
    all, and this test's ensureVision=nil means no OCR ever reads it."""
    img = Image.new("RGB", (640, 280), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle((4, 4, 635, 275), outline=(0, 0, 0), width=2)
    draw.text((20, 20), "FIRST SYNTHETIC BANK", fill=(0, 0, 0))
    draw.text((480, 20), "1001", fill=(0, 0, 0))
    draw.text((20, 90), "PAY TO THE ORDER OF   Jane Q. Sample", fill=(0, 0, 0))
    draw.text((480, 90), "$ 50.00", fill=(0, 0, 0))
    draw.text((20, 200), "MEMO   Test payment", fill=(0, 0, 0))
    draw.text((20, 240), ":123456789: 000111222333: 1001", fill=(0, 0, 0))
    return img


def main():
    check_img = _draw_placeholder_check()
    buf = io.BytesIO()
    check_img.save(buf, format="JPEG", quality=85)
    check_jpeg = buf.getvalue()

    pages = [
        build_statement_page(),
        build_check_images_page(check_jpeg, check_img.width, check_img.height),
    ]
    path = os.path.join(HERE, "bofa-stmt-checks-synthetic-sample.pdf")
    build_pdf(pages, path)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
