"""OCR for check images embedded in bank statement PDFs.

Some checking-account statements print a "Check images" section: scans
of every cleared check, each preceded by a printed "Check number: N |
Amount: $X.XX" label - two real-world page layouts seen so far (see
find_check_image_pages): one full-page check scan per page with even
the labels rasterized into the image (none of that page's content is
extractable text), or several smaller per-check images on one page
alongside the labels as real extractable text. Either way, the
handwritten part of a check (payee, memo, signature) is only ever
readable via OCR. The check number and amount are already parsed far
more reliably from the statement's own text-based "Checks" summary
section (see bofa_checking.py); this module only adds the handwritten
payee and memo, and correlates them back to that already-known list by
check number.

OCR here means calling out to a real vision-language model - see
README_bankcheck_ocr.md for why: PaddleOCR and TrOCR, tried locally in
isolation, performed far worse (TrOCR actively hallucinated fluent,
unrelated English on this handwriting) than a general vision-capable
chat model. Rather than bundle a dedicated model just for this, callers
pass in whichever vision-capable endpoint is already running as the
user's configured main LLM (see pkg/ingest.EnsureVisionModelFunc on the
Go side); the actual HTTP call is in the sibling vision_client.py
module, shared with redact_pdf_text.py's in-image redaction - so this
codebase never needs an extra Python dependency just to reach it.
"""

import base64
import io
import re
import sys
import urllib.error

import vision_client

# Asks for every check on the page in one call (rather than cropping and
# calling per-check) - a "Check images" page holds up to a dozen checks
# at ample per-check resolution (rendered at 300dpi), and this avoids
# needing brittle pixel-projection segmentation to crop individual
# checks (redaction bars and uneven scan skew made that unreliable in
# testing - see README_bankcheck_ocr.md).
_PAGE_PROMPT = (
    "This image shows a bank statement's \"Check images\" page: a grid of "
    "scanned personal checks, each preceded by a printed \"Check number: "
    "N | Amount: $X.XX\" label. For every check pictured, read its "
    "printed check number and the handwritten \"Pay to the order of\" "
    "line (payee) and \"For\" / memo line at the bottom left. Reply with "
    "ONLY a JSON array, one object per check, in this exact shape and no "
    "other text:\n"
    '[{"check_number": "1409", "payee": "...", "memo": "..."}, ...]\n'
    "Use an empty string for payee or memo you cannot read confidently, "
    "or if the memo line is blank - never guess."
)

# Pages with more than this many characters of real extracted text
# aren't check-image pages under layout A (see find_check_image_pages).
_MAX_TEXT_LEN = 60

# An image covering less of the page than this fraction is assumed to be
# decorative (a header logo, e.g. the small Bank of America wordmark
# printed above each layout-B check images page) rather than an actual
# check face - confirmed empirically: a real check face image on a
# layout-B page covers ~4% of the page, a logo ~0.8%, comfortable margin
# either side of this threshold.
_MIN_CHECK_IMAGE_AREA_FRACTION = 0.02


def _image_area_fraction(im: dict, page) -> float:
    width_frac = (im["x1"] - im["x0"]) / float(page.width)
    height_frac = (im["bottom"] - im["top"]) / float(page.height)
    return width_frac * height_frac


def find_check_image_pages(pdf) -> list[int]:
    """Returns indices of pdf's check-image pages. Two real-world layouts
    seen so far (see module docstring for the general shape both share -
    printed "Check number: N | Amount: $X.XX" labels, handwritten payee/
    memo only readable via OCR):

    A) Essentially blank pages (at most a page-footer line of real
       extracted text) holding one full-page check scan, with even the
       "Check number"/"Amount" labels rasterized into the image itself
       (confirmed against test/bofa-sample-statement.pdf).

    B) Pages with a literal "Check images" section header and the
       "Check number"/"Amount" labels as real extractable text, each
       check pictured as its own smaller image (more than one per page)
       rather than one image filling the page (confirmed against
       test/bofa-stmt-checks.pdf) - layout A's negligible-text-and-one-
       full-page-image test doesn't match this at all.
    """
    pages = []
    for i, page in enumerate(pdf.pages):
        text = (page.extract_text() or "").strip()
        imgs = page.images

        if len(text) <= _MAX_TEXT_LEN and len(imgs) == 1:
            im = imgs[0]
            width_frac = (im["x1"] - im["x0"]) / float(page.width)
            height_frac = (im["bottom"] - im["top"]) / float(page.height)
            if width_frac > 0.8 and height_frac > 0.8:
                pages.append(i)
                continue

        if "check images" in text.lower():
            if any(_image_area_fraction(im, page) >= _MIN_CHECK_IMAGE_AREA_FRACTION for im in imgs):
                pages.append(i)
    return pages


def _parse_checks_json(text: str) -> list[dict]:
    return [d for d in vision_client.extract_json_array(text) if isinstance(d, dict)]


def enrich_checks(checks: list[dict], pdf, vision: dict) -> None:
    """Mutates checks (the list of {"date", "description", "amount",
    "balance"} dicts built from the statement's text-based Checks
    section - see bofa_checking.py, description always "CHECK #N" at
    this point) in place: for every check-image page found, calls the
    vision endpoint and appends any matched check's OCR'd payee/memo to
    its description.

    Never raises: check-image OCR is best-effort enrichment on top of
    already-correct check data (number, date, amount), so any failure
    (network, bad JSON, no vision-capable model reachable) just leaves
    the affected checks with their plain "CHECK #N" description rather
    than breaking statement parsing.
    """
    try:
        pages = find_check_image_pages(pdf)
        if not pages:
            return
        by_number = {}
        for c in checks:
            m = re.match(r"CHECK #(\d+)", c["description"])
            if m:
                by_number[m.group(1)] = c

        for page_index in pages:
            page = pdf.pages[page_index]
            image = page.to_image(resolution=300).original.convert("RGB")
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            image_b64 = base64.b64encode(buf.getvalue()).decode()

            try:
                text = vision_client.call_vision(vision, image_b64, _PAGE_PROMPT)
            except (urllib.error.URLError, OSError, TimeoutError, KeyError, ValueError) as e:
                print(f"check_ocr: vision call failed on page {page_index}: {e}", file=sys.stderr)
                continue

            for entry in _parse_checks_json(text):
                num = str(entry.get("check_number", "")).lstrip("#").strip()
                check = by_number.get(num)
                if check is None:
                    continue
                payee = str(entry.get("payee", "")).strip()
                if not payee:
                    continue
                memo = str(entry.get("memo", "")).strip()
                desc = check["description"] + f" to {payee}"
                if memo:
                    desc += f" re: {memo}"
                check["description"] = desc
    except Exception as e:  # noqa: BLE001
        print(f"check_ocr: enrichment failed, leaving checks unenriched: {e}", file=sys.stderr)
