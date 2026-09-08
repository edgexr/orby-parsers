#!/usr/bin/env python3
"""Generates test/brokerage-csv-synthetic-sample.csv, the fixture behind
TestExtractTransactionsSampleBrokerageCSVSynthetic in pkg/ingest.

Every name, merchant, amount and account number here is invented, so
unlike a real export this fixture can live in git and give the
brokerage-CSV pipeline (csv_statement.py's combined KIND_BROKERAGE
dispatch, see pkg/ingest/py/csv_institutions/sample_brokerage_csv.py)
regression coverage that runs for everyone - the CSV sibling of
test/gen-brokerage-synthetic-sample.py's PDF fixture.

Regenerate with:  python3 test/gen-brokerage-csv-synthetic-sample.py
"""

import csv
import os

ROWS = [
    ["Trade Date", "Action", "Symbol", "Description", "Quantity", "Price", "Amount"],
    ["06/03/2026", "Buy", "AAPL", "Bought 2 shares of AAPL", "2.000", "205.00", "-410.00"],
    ["06/15/2026", "Dividend", "VOO", "Dividend Vanguard S&P 500 ETF", "", "", "12.45"],
    ["06/28/2026", "Sell", "AAPL", "Sold 1 share of AAPL", "-1.000", "215.00", "215.00"],
]


def main():
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures")

    csv_path = os.path.join(here, "brokerage-csv-synthetic-sample.csv")
    with open(csv_path, "w", newline="") as f:
        csv.writer(f).writerows(ROWS)
    print(f"wrote {csv_path}")

    # The same rows as an .xlsx workbook - csv_statement.py reads both
    # formats through the one csv_institutions/sample_brokerage_csv.py
    # parser, and TestExtractTransactionsSampleBrokerageXLSXSynthetic
    # asserts the two fixtures produce identical output.
    import openpyxl  # only needed to regenerate the fixture, not to run tests

    xlsx_path = os.path.join(here, "brokerage-xlsx-synthetic-sample.xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Transactions"
    for row in ROWS:
        ws.append(row)
    wb.save(xlsx_path)
    print(f"wrote {xlsx_path}")

    print(f"  {len(ROWS) - 1} transactions")


if __name__ == "__main__":
    main()
