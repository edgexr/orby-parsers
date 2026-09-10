#!/usr/bin/env python3
"""Generates test/fidelity-synthetic-year-end-2025.pdf - a Fidelity
"YEAR-END INVESTMENT REPORT" in the annual layout, from wholly invented
data so it can be committed and run everywhere.

Why this exists separately from gen-fidelity-synthetic-sample.py (the
monthly statements): the year-end report's Holdings tables drop the
leading "Beginning Market Value" (Jan 1) column every monthly/quarterly
statement carries. Its columns are

    Description | Quantity | Price Per Unit | Total Market Value |
    Total Cost Basis | Total Unrealized Gain/Loss | Income Earned

Read with the monthly row regex, each row shifts one cell left: "Total
Cost Basis" lands in current_value and the parser's own "Total Holdings"
reconciliation fails by the market-value/cost-basis difference. This
fixture is that layout, minimal but faithful - one holding per section
(Core Account, Mutual Funds, Stocks), the section running across a page
break and reprinting its own header, a wrapped ticker line, a
"not applicable" cost basis on the core position, a negative unrealized
gain, and the closing legal text that names retirement wrappers
generically (so the test can assert account type stays "Brokerage").

Every printed total is summed from the rows, not typed, because
fidelity_brokerage.py validates its parsed holdings against
"Total Holdings" and refuses a statement that does not add up - so this
script's own arithmetic is the fixture's first test.

Regenerate with:  python3 test/gen-fidelity-year-end-sample.py
"""

import json
import os

PAGE_W, PAGE_H = 612, 792
FONT_SIZE = 8
LINE_H = 11
SIDEBAR_TOP = PAGE_H - 40
TOP = PAGE_H - 95
X_DESC = 31.4
X_WRAP = 33.6
X_SIDEBAR = 738.4
X_FOOTER = 723.6

PERIOD = ("January 1, 2025", "December 31, 2025")
CLOSE_DATE = "2025-12-31"
# More digits than the usual last-4 redaction so the fixture's disclosed
# identifier and the last-4 label the parser emits as "account" differ.
ACCOUNT_DIGITS = "56784491"
ACCOUNT_LAST4 = ACCOUNT_DIGITS[-4:]
REGISTRATION = "SYNTHETIC REVOCABLE FAMILY TRUST - TRUST: UNDER AGRE"
REPORT_TITLE = "2025 YEAR-END INVESTMENT REPORT"


def money(v, dollar=False, places=2):
    s = f"{abs(v):,.{places}f}"
    if dollar:
        s = "$" + s
    return ("-" + s) if v < 0 else s


class Holding:
    def __init__(self, name_lines, symbol, quantity, price, market_value,
                 cost_basis, income_earned):
        self.name_lines = name_lines      # printed name, split how Fidelity wraps it
        self.symbol = symbol
        self.quantity = quantity
        self.price = price
        self.market_value = market_value
        self.cost_basis = cost_basis      # None -> "not applicable" (core position)
        self.income_earned = income_earned

    @property
    def gain(self):
        return None if self.cost_basis is None else round(self.market_value - self.cost_basis, 2)


# One holding per section. Values chosen so the section subtotals and the
# grand total are the kind of figures a real statement carries.
CORE = [
    Holding(["SYNTHETIC GOVERNMENT MONEY", "MARKET (SYNXX)"], "SYNXX",
            898.840, 1.0000, 898.84, None, 34.15),
]
MUTUAL_FUNDS = [
    Holding(["SYNTHETIC 500 INDEX FUND (SYNFX)"], "SYNFX",
            1_511.745, 237.7200, 359_372.02, 211_246.24, 3_973.93),
]
STOCKS = [
    Holding(["ZENITH ALPHA CORP COM USD0.01 (ZALP)"], "ZALP",
            275.816, 228.4900, 63_021.19, 42_825.13, 1_774.46),
    Holding(["ZENITH BETA INC (ZBET)"], "ZBET",
            162.134, 271.8600, 44_077.74, 25_619.84, 166.56),
    Holding(["ZENITH GAMMA CO (ZGAM)"], "ZGAM",
            875.332, 69.9100, 61_194.46, 52_994.05, 1_753.21),
    Holding(["ZENITH DELTA HOLDINGS COM USD0.50", "(ZDEL)"], "ZDEL",
            105.573, 241.1600, 25_459.98, 20_919.29, 490.07),
    # A negative unrealized gain - the row shape that makes the
    # gain/loss column carry a leading minus.
    Holding(["ZENITH EPSILON CORP (ZEPS)"], "ZEPS",
            121.780, 143.5200, 17_477.86, 21_406.27, 659.98),
]

ALL_HOLDINGS = CORE + MUTUAL_FUNDS + STOCKS


def subtotal(rows):
    value = round(sum(h.market_value for h in rows), 2)
    cost = round(sum(h.cost_basis for h in rows if h.cost_basis is not None), 2)
    gain = round(sum(h.gain for h in rows if h.gain is not None), 2)
    income = round(sum(h.income_earned for h in rows), 2)
    return value, cost, gain, income


class Page:
    def __init__(self):
        self.items = []
        self.y = TOP

    def at(self, x, s):
        self.items.append((x, self.y, s))

    def line(self, s, x=X_DESC):
        self.at(x, s)
        self.y -= LINE_H

    def blank(self, n=1):
        self.y -= LINE_H * n


class Statement:
    def __init__(self):
        self.pages = []

    def new_page(self, page_title, repeated_header=True):
        p = Page()
        self.pages.append(p)
        # The rotated mail-sort codes, dropped by the parser because they
        # sit above the period line its boilerplate strip anchors on.
        y = SIDEBAR_TOP
        for code in ("S", "80106202", "BBBBB_SYNTHBBBB_", "EC_RY"):
            p.items.append((X_SIDEBAR, y, code))
            y -= LINE_H
        p.y = TOP
        p.line(REPORT_TITLE)
        p.line(f"{PERIOD[0]} - {PERIOD[1]}")
        if repeated_header:
            # These three land in the header slot on every holdings page,
            # so the parser strips them by repetition (Holdings is also a
            # known page title).
            p.line(f"Account # XXXXXXXX{ACCOUNT_DIGITS}")
            p.line(page_title)
            p.line(REGISTRATION)
        return p

    def holdings_header(self, page):
        page.line("Price Total Total Unrealized", x=215.0)
        page.line("Description Quantity Per Unit Market Value Cost Basis "
                  "Gain/Loss Income Earned")

    def emit_holding(self, page, h):
        cost = "not applicable" if h.cost_basis is None else money(h.cost_basis, dollar=True)
        gain = "not applicable" if h.gain is None else money(h.gain, dollar=True)
        cells = [
            money(h.quantity, places=3),
            money(h.price, dollar=True, places=4),
            money(h.market_value, dollar=True),
            cost,
            gain,
            money(h.income_earned, dollar=True),
        ]
        page.line(f"{h.name_lines[0]} " + " ".join(cells))
        for w in h.name_lines[1:]:
            page.line(w, x=X_WRAP)

    def emit_core_subtotal(self, page, rows):
        value, _cost, _gain, income = subtotal(rows)
        page.line(f"Total Core Account (0% of account {money(value, dollar=True)} "
                  f"{money(income, dollar=True)}")
        page.line("holdings)")

    def emit_subtotal(self, page, label, rows, pct):
        value, cost, gain, income = subtotal(rows)
        page.line(f"{label} ({pct}% of account {money(value, dollar=True)} "
                  f"{money(cost, dollar=True)} {money(gain, dollar=True)} "
                  f"{money(income, dollar=True)}")
        page.line("holdings)")

    def build(self):
        grand_value, grand_cost, grand_gain, grand_income = subtotal(ALL_HOLDINGS)

        # --- cover page ---
        p = self.new_page("Cover", repeated_header=False)
        p.line("Envelope # BSYNTHBBBBZZZ")
        p.line("SYNTHETIC XXXXXXXX")
        p.line("XXXX XXXXX XX")
        p.blank()
        p.line("FIDELITY ACCOUNT SYNTHETIC REVOCABLE FAMILY TRUST")
        p.line("U/A 01/01/20 SYNTHETIC X SYNTHETIC AND XXXX XXXX TRUSTEES")
        p.line(f"Account Number: XXXXXXXX{ACCOUNT_DIGITS}")
        p.line(f"Your Account Value: {money(grand_value, dollar=True)}")
        p.blank()
        p.line(f"Beginning Account Value as of Jan 1, 2025 {money(451_083.63, dollar=True)}")
        p.line(f"Ending Account Value as of Dec 31, 2025 {money(grand_value, dollar=True)}")
        p.line("Brokerage services provided by Synthetic Brokerage Services LLC, Member NYSE, SIPC.")

        # --- holdings page 1: Core Account, Mutual Funds, start of Stocks ---
        p = self.new_page("Holdings")
        p.line("Holdings")
        p.line("Core Account")
        self.holdings_header(p)
        for h in CORE:
            self.emit_holding(p, h)
        p.line("-- 7-day yield: 3.43%", x=X_WRAP)
        self.emit_core_subtotal(p, CORE)

        p.line("Mutual Funds")
        self.holdings_header(p)
        p.line("Stock Funds")
        for h in MUTUAL_FUNDS:
            self.emit_holding(p, h)
        self.emit_subtotal(p, "Total Stock Funds", MUTUAL_FUNDS, 47)
        self.emit_subtotal(p, "Total Mutual Funds", MUTUAL_FUNDS, 47)

        p.line("Stocks")
        self.holdings_header(p)
        p.line("Common Stock")
        for h in STOCKS[:2]:
            self.emit_holding(p, h)

        # --- holdings page 2: the section runs across the page break ---
        p = self.new_page("Holdings")
        p.line("Holdings")
        p.line("Stocks (continued)")
        self.holdings_header(p)
        p.line("Common Stock (continued)")
        for h in STOCKS[2:]:
            self.emit_holding(p, h)
        stocks_value, stocks_cost, stocks_gain, stocks_income = subtotal(STOCKS)
        self.emit_subtotal(p, "Total Common Stock", STOCKS, 53)
        p.line(f"Total Stocks (53% of account holdings) {money(stocks_value, dollar=True)} "
               f"{money(stocks_cost, dollar=True)} {money(stocks_gain, dollar=True)} "
               f"{money(stocks_income, dollar=True)}")
        p.line(f"Total Holdings {money(grand_value, dollar=True)} "
               f"{money(grand_cost, dollar=True)} {money(grand_gain, dollar=True)} "
               f"{money(grand_income, dollar=True)}")
        p.line("All positions held in cash account unless indicated otherwise.")

        # --- endnotes: the generic retirement-wrapper legal text ---
        p = self.new_page("Additional Information and Endnotes")
        p.line("Additional Information and Endnotes")
        p.line("Income Summary Shows income by tax status for the statement and "
               "year-to-date periods. In Traditional IRAs, Rollover IRAs, SEP-IRAs,")
        p.line("SIMPLE IRAs and Keoghs, earnings are reported as tax-deferred income. "
               "In Roth IRAs and HSAs, earnings are reported as tax-exempt income.")

        for i, page in enumerate(self.pages):
            page.at(X_FOOTER, f"{i + 1} of {len(self.pages)}")

        self.total_holdings = grand_value
        self.core_value = subtotal(CORE)[0]
        return self.pages

    def expectations(self, filename):
        return {
            "file": filename,
            "statementDate": CLOSE_DATE,
            "account": ACCOUNT_LAST4,
            # "Trust", not "Brokerage": this fixture's registration is a
            # revocable family trust, and the shared account-type table
            # (common.ACCOUNT_TYPE_PATTERNS) recognizes that where the
            # Fidelity-only table it replaced did not. A revocable trust
            # is still taxable - see TreatmentOf - so only the label is
            # more specific, not the tax treatment.
            "accountType": "Trust",
            "totalHoldings": self.total_holdings,
            "coreValue": self.core_value,
            "positions": {h.symbol: round(h.market_value, 2) for h in ALL_HOLDINGS},
            "costBasis": {
                h.symbol: (None if h.cost_basis is None else round(h.cost_basis, 2))
                for h in ALL_HOLDINGS
            },
        }


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

    for i, page in enumerate(pages):
        page_num, content_num = page_obj_nums[i], page_obj_nums[i] + 1
        add_obj(page_num, (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_num} 0 R >>"
        ).encode())
        stream = bytearray()
        for x, y, s in page.items:
            stream.extend(f"BT /F1 {FONT_SIZE} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm (".encode())
            stream.extend(escape(s))
            stream.extend(b") Tj ET\n")
        add_obj(content_num, b"<< /Length " + str(len(stream)).encode() +
                b" >>\nstream\n" + bytes(stream) + b"endstream")

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
    name = "fidelity-synthetic-year-end-2025.pdf"
    stmt = Statement()
    pages = stmt.build()
    build_pdf(pages, os.path.join(here, name))

    sidecar = os.path.join(here, "fidelity-synthetic-year-end-expectations.json")
    with open(sidecar, "w") as f:
        json.dump(stmt.expectations(name), f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"wrote {name}: {len(pages)} pages, "
          f"total holdings {money(stmt.total_holdings, dollar=True)}, "
          f"core {money(stmt.core_value, dollar=True)}")
    print("wrote fidelity-synthetic-year-end-expectations.json")


if __name__ == "__main__":
    main()
