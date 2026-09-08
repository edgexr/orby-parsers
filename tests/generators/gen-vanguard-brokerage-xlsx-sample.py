#!/usr/bin/env python3
"""Generates test/vanguard-brokerage-xlsx-synthetic-sample.xlsx, the
fixture behind TestExtractTransactionsVanguardBrokerageXLSXSynthetic in
pkg/ingest.

Reproduces the real shape of a Vanguard "Custom Activity Report" .xlsx -
two preamble lines, the 10-column activity header, activity rows, then a
DISCLOSURES footer block - with wholly invented fund names, dates and
amounts, so this fixture can live in git and give the .xlsx pipeline
(csv_statement.py's openpyxl reader + csv_institutions/vanguard_brokerage.py)
regression coverage that runs for everyone.

Requires openpyxl.  Regenerate with:
    python3 test/gen-vanguard-brokerage-xlsx-sample.py
"""

import os

import openpyxl

PREAMBLE = [
    ["Custom report created on: 09/01/2026."],
    ["This report only includes transactions that were settled from: 01/01/2020 to 09/01/2026."],
]

HEADER = [
    "Settlement date", "Trade date", "Symbol", "Name", "Type",
    "Account type", "Quantity", "Price", "Commission & fees**", "Amount",
]

# date, trade date, symbol, name, type, acct type, qty, price, fees, amount
ACTIVITY = [
    ["1/15/2024", "1/13/2024", "VFIAX", "Vanguard 500 Index Fund Admiral", "Dividend", "CASH", "", "", "", "$42.18"],
    ["2/01/2024", "1/31/2024", "VFIAX", "Vanguard 500 Index Fund Admiral", "Reinvestment", "CASH", "0.0910", "$463.52", "", "-$42.18"],
    ["3/12/2024", "3/10/2024", "VTSAX", "Vanguard Total Stock Market Admiral", "Buy", "CASH", "5.0000", "$120.00", "$1.00", "-$601.00"],
    ["3/18/2024", "3/16/2024", "VTIAX", "Vanguard Total International Stock Admiral", "Buy", "CASH", "3.0000", "$40.00", "Free", "-$120.00"],
    ["4/20/2024", "4/18/2024", "VTSAX", "Vanguard Total Stock Market Admiral", "Sell", "CASH", "2.0000", "$125.50", "$1.00", "$250.00"],
    ["5/03/2024", "5/01/2024", "VMFXX", "Vanguard Federal Money Market Fund", "Dividend", "CASH", "", "", "", "$3.07"],
    ["6/30/2024", "6/28/2024", "VFIAX", "Vanguard 500 Index Fund Admiral", "Capital gain (LT)", "CASH", "", "", "", "$18.44"],
    ["7/15/2024", "", "VTSAX", "Vanguard Total Stock Market Admiral", "TRANSFER FROM BROKERAGE", "CASH", "10.0000", "", "", ""],
]

DISCLOSURES = [
    ["DISCLOSURES"],
    ["*Note on account protection: Securities in your brokerage account are held in custody by a synthetic custodian for this test fixture."],
    ["**The fees displayed include but are not limited to commissions and transaction fees. This is invented text for a test fixture."],
    ["This report is not intended to replace an account statement or confirmation."],
    ["(C) 2026 Synthetic Test Data. Not a real Vanguard document."],
]


def main() -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Transaction History"
    for row in PREAMBLE:
        ws.append(row)
    ws.append(HEADER)
    for row in ACTIVITY:
        ws.append(row)
    for row in DISCLOSURES:
        ws.append(row)

    path = os.path.join(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures"),
        "vanguard-brokerage-xlsx-synthetic-sample.xlsx",
    )
    wb.save(path)
    print(f"wrote {path}")
    print(f"  {len(ACTIVITY)} activity rows")


if __name__ == "__main__":
    main()
