"""Unit tests for parser_common's spreadsheet header/footer helpers
(looks_like_header_row / grid_header_and_rows), used by csv_statement.py
for both the CSV and .xlsx paths.

Run:  ~/.orby/ingest/venv/bin/python3 -m unittest discover -s test -p '*_test.py'
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pkg", "ingest", "py"))
import parser_common  # noqa: E402


class HeaderRowTest(unittest.TestCase):
    def test_looks_like_header_row(self):
        self.assertTrue(parser_common.looks_like_header_row(["Date", "Description", "Amount"]))
        self.assertTrue(parser_common.looks_like_header_row(["Settlement date", "Symbol", "Amount", "", ""]))
        # a data row: has a date and a number
        self.assertFalse(parser_common.looks_like_header_row(["2024-01-02", "Coffee", "-5.00"]))
        self.assertFalse(parser_common.looks_like_header_row(["8/30/2020", "VMRAX", "$74.28"]))
        # a one-cell preamble / footer line
        self.assertFalse(parser_common.looks_like_header_row(["Custom report created on: 09/01/2026."]))
        self.assertFalse(parser_common.looks_like_header_row(["", "", ""]))

    def test_grid_header_and_rows_strips_preamble_and_footer(self):
        grid = [
            ["Custom report created on: 09/01/2026.", "", ""],
            ["This report only includes ...", "", ""],
            ["Settlement date", "Symbol", "Amount"],
            ["8/30/2020", "VMRAX", ""],
            ["3/19/2024", "VMRAX", "$74.29"],
            [],
            ["DISCLOSURES"],
            ["*Note on account protection ..."],
        ]
        header, data = parser_common.grid_header_and_rows(grid)
        self.assertEqual(header, ["Settlement date", "Symbol", "Amount"])
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0], ["8/30/2020", "VMRAX", ""])

    def test_grid_header_and_rows_plain_table(self):
        grid = [["Date", "Amount"], ["2024-01-01", "-5.00"], ["2024-01-02", "10.00"]]
        header, data = parser_common.grid_header_and_rows(grid)
        self.assertEqual(header, ["Date", "Amount"])
        self.assertEqual(len(data), 2)

    def test_grid_header_and_rows_no_header_falls_back(self):
        grid = [["a"], ["b"]]
        header, data = parser_common.grid_header_and_rows(grid)
        self.assertEqual(header, ["a"])
        self.assertEqual(data, [["b"]])


if __name__ == "__main__":
    unittest.main()
