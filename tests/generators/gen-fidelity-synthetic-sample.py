#!/usr/bin/env python3
"""Generates test/fidelity-synthetic-<period>.pdf - three consecutive
monthly statements in Fidelity's "INVESTMENT REPORT" layout, from wholly
invented data.

Why this exists. The Fidelity parser
(pkg/ingest/py/institutions/fidelity_brokerage.py) was written against
six redacted real statements, and .gitignore keeps every test/*.pdf out
of git by default because a redaction is not a guarantee. So those tests
skip everywhere except the machine that has the samples - which is to
say the parser has no CI coverage at all. These fixtures are invented
end to end, so they can be committed and run for everyone.

What makes them worth generating rather than hand-writing: each real
statement taught the parser something that only shows up in the *text
layout*, and a simple fixture would exercise none of it. Reproduced here,
with the statement each was learned from:

  * the margin-account marker, a bare "M" in a gutter left of the
    Description column that extract_text() glues onto the name
    ("MZQAA"), emitted here as two text runs at the same x-offsets the
    real statements use so the gluing happens for the same reason
  * the same security held as both a cash and a margin lot, which are
    two separate positions with their own quantity and cost basis
  * an Options table, one column narrower than every other holdings
    table (no EAI/EY), with short positions carrying negative value and
    OCC contract codes rather than the underlying's ticker
  * a sub-total whose figures print on the line *above* its label, and
    - in one month - on the same line as it, which reads as a
    continuation of the holding above unless the leading figures are
    stripped first
  * a trade flagged with the lowercase specific-share-identification
    marker, glued onto the date as "s07/27"
  * realized gain/loss notes both on their own line and trailing the
    contract description inline, and a "TRADE DATE" annotation split
    across the lines it wraps over
  * a bare "Interest" description, where every other income row says
    "Dividend Received" or "Reinvestment"
  * a deposit and a withdrawal on one day, netted by the core account
    into a single sweep row
  * a zero closing balance printed as "-" rather than $0.00
  * a merger moving a position between two CUSIPs with no cash amount
  * sections that run across a page break and reprint their own header
  * two different redaction styles for the account number - one keeping
    the "Account #" label, one masking the label itself
  * the closing legal text that names "Traditional IRAs, Rollover IRAs,
    SEP-IRAs, SIMPLE IRAs" generically, which is why account type is
    read from the account title block and not the whole document

Every printed total is computed from the rows rather than typed, because
the parser validates itself against them and will refuse a statement
whose sections do not add up. That makes this script's own arithmetic
the fixture's first test.

Regenerate with:  python3 test/gen-fidelity-synthetic-sample.py
"""

import json
import os

PAGE_W, PAGE_H = 612, 792
FONT_SIZE = 8
LINE_H = 11
# Content starts below the rotated mail-sort block, so those codes land
# on their own extracted lines rather than being merged into the report
# header - which is where the parser anchors its per-page boilerplate
# strip, and what the real statements do.
SIDEBAR_TOP = PAGE_H - 40
TOP = PAGE_H - 95

# Fidelity prints the margin marker in a narrow gutter left of the
# Description column. The gap is small enough that extract_text() runs
# the two together, which is the whole reason the parser has to go back
# to the PDF for word x-coordinates - so the fixture has to reproduce
# the geometry, not just the glued string.
X_GUTTER = 24.2
X_DESC = 31.4
X_WRAP = 33.6          # a wrapped description line, indented slightly
X_SIDEBAR = 738.4      # rotated mail-sort codes, far off to the right
X_FOOTER = 723.6

# 8 digits, not the usual last-4 redaction, so the fixture's full
# disclosed identifier and the last-4 label the parser is supposed to
# emit as "account" actually differ - a fixture that only ever leaves 4
# digits visible can't catch a parser that regresses to storing the
# full identifier as "account" instead of truncating it (see
# ACCOUNT_LAST4 below, and fidelity_brokerage.py's _account_identity).
ACCOUNT_DIGITS = "56783444"
ACCOUNT_LAST4 = ACCOUNT_DIGITS[-4:]
REGISTRATION = "XXXXX XXXXXXXX - SYNTHETIC JOINT WROS - TOD"

# --- securities ------------------------------------------------------
# name is what the statement prints, split the way Fidelity wraps it.
SECURITIES = {
    "ZQAA": {"name": ["ZENITH QUANTUM ALPHA ETF"], "cusip": "111111AA1",
             "section": "Exchange Traded Products", "sub": "Equity ETPs"},
    "ZQBB": {"name": ["ZENITH QUANTUM BOND", "INDEX ETF"], "cusip": "222222BB2",
             "section": "Exchange Traded Products", "sub": "Fixed Income ETPs"},
    "ZQCC": {"name": ["ZENITH QUANTUM COMMODITY", "TRACKER ETF"], "cusip": "333333CC3",
             "section": "Exchange Traded Products", "sub": "Other ETPs"},
    "ZQDD": {"name": ["ZENITH DYNAMICS CORP COM"], "cusip": "444444DD4",
             "section": "Stocks", "sub": "Common Stock"},
    "ZQEE": {"name": ["ZENITH ENERGY TRUST COM STK", "USD0.01"], "cusip": "555555EE5",
             "section": "Stocks", "sub": "Common Stock"},
    "ZQFF": {"name": ["ZENITH FUSION HOLDINGS COM"], "cusip": "666666FF6",
             "section": "Stocks", "sub": "Common Stock"},
    "ZQGG": {"name": ["ZENITH GLOBAL MUNI FD INC", "COM STK USD0.1"], "cusip": "777777GG7",
             "section": "Stocks", "sub": "Common Stock"},
    "SYNXX": {"name": ["SYNTHETIC GOVERNMENT MONEY", "MARKET"], "cusip": "999999XX9",
              "section": "Core Account", "sub": None},
    "CASH": {"name": ["CASH"], "cusip": "315994103",
             "section": "Core Account", "sub": None},
}


def money(v, dollar=False, places=2):
    """Formats like the statement does: thousands separators, and the
    minus sign outside the dollar sign."""
    s = f"{abs(v):,.{places}f}"
    if dollar:
        s = "$" + s
    return ("-" + s) if v < 0 else s


class Holding:
    def __init__(self, symbol, quantity, price, cost_basis, begin_value,
                 margin=False, eai=None, option=None):
        self.symbol = symbol
        self.quantity = quantity
        self.price = price
        self.cost_basis = cost_basis
        self.begin_value = begin_value
        self.margin = margin
        self.eai = eai
        self.option = option          # OCC code + printed contract text

    @property
    def value(self):
        # An option contract covers 100 shares: the price is quoted per
        # share but the market value is per contract.
        multiplier = 100 if self.option else 1
        return round(self.quantity * self.price * multiplier, 2)

    @property
    def gain(self):
        return round(self.value - self.cost_basis, 2)


def core(qty, price=1.0, begin=0.0, eai=None):
    return Holding("SYNXX", qty, price, None, begin, eai=eai)


def old_core_cash(qty, price=1.0, begin=0.0, eai=None):
    return Holding("CASH", qty, price, None, begin, eai=eai)


# --- three monthly statements ---------------------------------------
# Balances chain: each month opens where the last one closed.

def month_one():
    """A plain month: no options, no margin, one deposit. Its account
    number keeps the "Account #" label, the redaction style the January
    real sample uses."""
    holdings = [
        core(52_000.00, begin=48_000.00, eai=1_820.00),
        Holding("ZQAA", 400.0, 51.25, 19_800.00, 19_600.00, eai=612.00),
        Holding("ZQBB", 1_500.0, 100.40, 150_100.00, 149_700.00, eai=6_030.00),
        Holding("ZQDD", 800.0, 214.50, 148_000.00, 146_200.00, margin=True, eai=1_240.00),
        Holding("ZQEE", 2_000.0, 18.75, 36_400.00, 36_000.00, margin=True, eai=1_500.00),
        # Held here and in February, gone by March: the March merger is
        # what takes it away, and a merger whose outgoing security was
        # never held cannot be linked to what replaced it (there is no
        # security to link *from*). See TestIngestFidelitySyntheticSecurities.
        Holding("ZQFF", 1_600.0, 105.00, 160_000.00, 166_400.00, eai=980.00),
    ]
    trades = [
        {"date": "01/08", "symbol": "ZQAA", "action": "You Bought", "qty": 400.0,
         "price": 49.50, "cost_basis": None, "fee": None, "amount": -19_800.00},
        {"date": "01/09", "symbol": "ZQBB", "action": "You Bought", "qty": 1_500.0,
         "price": 100.0667, "cost_basis": None, "fee": None, "amount": -150_100.00},
    ]
    income = [
        {"date": "01/24", "symbol": "ZQDD", "action": "Reinvestment", "qty": 1.402,
         "price": 212.55, "amount": -297.99, "trade_date": "01-23-26"},
        {"date": "01/24", "symbol": "ZQDD", "action": "Dividend Received",
         "qty": None, "price": None, "amount": 297.99},
        {"date": "01/30", "symbol": "SYNXX", "action": "Dividend Received",
         "qty": None, "price": None, "amount": 154.20},
    ]
    deposits = [{"date": "01/15", "description": "Wire Trans From Bank", "amount": 62_000.00}]
    withdrawals = []
    cash = [
        {"date": "01/08", "action": "You Sold", "amount": -19_800.00, "note": "MORNING TRADE @ 1"},
        {"date": "01/09", "action": "You Sold", "amount": -150_100.00, "note": "MORNING TRADE @ 1"},
        {"date": "01/15", "action": "You Bought", "amount": 62_000.00, "note": "@ 1"},
        {"date": "01/30", "action": "Reinvestment", "amount": 154.20, "note": "REINVEST @ $1.000"},
    ]
    return dict(
        period=("January 1, 2026", "January 31, 2026"), page_label="Jan",
        begin_value=565_900.00, opening_core=160_000.00,
        holdings=holdings, trades=trades, income=income,
        deposits=deposits, withdrawals=withdrawals, cash=cash,
        masked_account_label=False, subtotal_inline=False,
        merger=None, pending=[], adjustments=False,
    )


def month_two():
    """Options, a margin lot beside a cash lot of the same security, a
    deposit and a withdrawal netted into one sweep row, a bare "Interest"
    credit, a specific-share-marked trade with real fees, and a core
    balance that reaches zero and prints as "-"."""
    holdings = [
        core(41_318.51, begin=52_000.00, eai=1_446.00),
        Holding("ZQAA", 400.0, 53.10, 19_800.00, 20_500.00, eai=612.00),
        Holding("ZQAA", 12.5, 53.10, 655.00, 0.00, margin=True, eai=19.00),
        Holding("ZQBB", 1_500.0, 101.20, 150_100.00, 150_600.00, eai=6_030.00),
        Holding("ZQCC", 300.0, 44.80, 13_100.00, 0.00, margin=True, eai=None),
        Holding("ZQDD", 800.0, 221.75, 148_000.00, 171_600.00, margin=True, eai=1_240.00),
        Holding("ZQEE", 2_000.0, 19.10, 36_400.00, 37_500.00, margin=True, eai=1_500.00),
        Holding("ZQFF", 1_600.0, 107.50, 160_000.00, 168_000.00, eai=980.00),
        Holding("ZQDD", -3.0, 4.25, -2_400.00, 0.00, margin=True,
                option={"occ": "ZQDD260918C230", "text": ["SEP 18 26 $230 (100 SHS)", "SHT"],
                        "kind": "CALL", "underlying": "ZQDD",
                        "name": "ZENITH DYNAMICS CORP COM"}),
    ]
    trades = [
        {"date": "02/05", "symbol": "ZQCC", "action": "You Bought", "qty": 300.0,
         "price": 43.6667, "cost_basis": None, "fee": -0.72, "amount": -13_100.72},
        {"date": "02/11", "symbol": "ZQDD", "action": "You Sold", "qty": -3.0,
         "price": 8.00, "cost_basis": None, "fee": -0.66, "amount": 2_399.34,
         "option": {"occ": "ZQDD260918C230", "kind": "CALL", "underlying": "ZQDD",
                    "name": "ZENITH DYNAMICS CORP COM", "cusip": "8111119AA",
                    "text": ["SEP 18 26 $230 (100 SHS) OPENING", "TRANSACTION"]}},
        {"date": "02/24", "symbol": "ZQAA", "action": "You Bought", "qty": 12.5,
         "price": 52.40, "cost_basis": None, "fee": None, "amount": -655.00,
         "specific_share": True, "gain_note": "Short-term gain: $18.40"},
    ]
    income = [
        {"date": "02/06", "symbol": None, "action": "Interest", "qty": None,
         "price": None, "amount": 3.44, "name": ["FULLY PAID"], "cusip": "888888II8"},
        {"date": "02/26", "symbol": "ZQEE", "action": "Reinvestment", "qty": 21.5,
         "price": 19.05, "amount": -409.58, "trade_date": "02-25-26", "split_trade_date": True},
        {"date": "02/26", "symbol": "ZQEE", "action": "Dividend Received",
         "qty": None, "price": None, "amount": 409.58},
    ]
    deposits = [{"date": "02/12", "description": "Wire Trans From Bank", "amount": 45_000.00}]
    withdrawals = [{"date": "02/12", "description": "Money Line Paid EFT FUNDS PAID ED11223344 /WEB",
                    "amount": -9_000.00, "note": "SYNTHETIC BANK NA ******1234"}]
    cash = [
        {"date": "02/05", "action": "You Sold", "amount": -13_100.72, "note": "MORNING TRADE @ 1"},
        {"date": "02/09", "action": "You Sold", "amount": -38_899.28, "note": "MORNING TRADE @ 1",
         "zero_balance": True},
        {"date": "02/11", "action": "You Bought", "amount": 2_399.34, "note": "@ 1"},
        {"date": "02/12", "action": "You Bought", "amount": 36_000.00, "note": "@ 1"},
        {"date": "02/24", "action": "You Sold", "amount": -655.00, "note": "MORNING TRADE @ 1"},
        {"date": "02/26", "action": "Reinvestment", "amount": 409.58, "note": "REINVEST @ $1.000"},
        {"date": "02/27", "action": "You Bought", "amount": 3.44, "note": "@ 1"},
    ]
    return dict(
        period=("February 1, 2026", "February 28, 2026"), page_label="Feb",
        begin_value=None, opening_core=52_000.00,
        holdings=holdings, trades=trades, income=income,
        deposits=deposits, withdrawals=withdrawals, cash=cash,
        masked_account_label=True, subtotal_inline=False,
        merger=None, pending=[], adjustments=True,
    )


def month_three():
    """A merger moving a position between CUSIPs, a sub-total printed on
    the same line as its own label, and trades that settle next month."""
    holdings = [
        core(48_902.11, begin=41_318.51, eai=1_711.00),
        Holding("ZQAA", 412.5, 54.90, 20_455.00, 21_240.00, eai=631.00),
        Holding("ZQBB", 1_500.0, 100.85, 150_100.00, 151_800.00, eai=6_030.00),
        Holding("ZQCC", 300.0, 46.15, 13_100.00, 13_440.00, margin=True, eai=None),
        Holding("ZQGG", 1_960.0, 89.40, 148_000.00, 177_400.00, margin=True, eai=1_240.00),
        Holding("ZQEE", 2_021.5, 19.55, 36_809.58, 38_610.65, margin=True, eai=1_516.00),
        Holding("ZQDD", -3.0, 2.10, -2_400.00, -1_275.00, margin=True,
                option={"occ": "ZQDD260918C230", "text": ["SEP 18 26 $230 (100 SHS)", "SHT"],
                        "kind": "CALL", "underlying": "ZQDD",
                        "name": "ZENITH DYNAMICS CORP COM"}),
    ]
    trades = [
        # An option written and bought back inside this one period, so
        # it reaches no Holdings section at all. That is the case a
        # name lookup against Holdings can never resolve, and it is the
        # one that carries the realized gain: in the real account these
        # round trips held every dollar of the realized total while
        # landing under an empty symbol. Its contract code has to come
        # out of the description.
        {"date": "03/05", "symbol": "ZQBB", "action": "You Sold", "qty": -2.0,
         "price": 6.20, "cost_basis": None, "fee": -0.66, "amount": 1_239.34,
         "option": {"occ": "ZQBB260417P95", "kind": "PUT", "underlying": "ZQBB",
                    "name": "ZENITH QUANTUM BOND INDEX ETF", "cusip": "8222229BB",
                    "text": ["APR 17 26 $95 (100 SHS) OPENING", "TRANSACTION"]}},
        {"date": "03/17", "symbol": "ZQAA", "action": "You Bought", "qty": 0.0,
         "price": 54.10, "cost_basis": None, "fee": -1.05, "amount": -1.05,
         "schedule": ["EX-DIV DATE 03/18/26 RECORD DATE 03/18/26",
                      "PAYABLE DTE 03/20/26"]},
        {"date": "03/20", "symbol": "ZQBB", "action": "You Bought", "qty": 2.0,
         "price": 2.15, "cost_basis": None, "fee": -0.60, "amount": -430.60,
         "gain_note": "Short-term gain: $808.74",
         "option": {"occ": "ZQBB260417P95", "kind": "PUT", "underlying": "ZQBB",
                    "name": "ZENITH QUANTUM BOND INDEX ETF", "cusip": "8222229BB",
                    "text": ["APR 17 26 $95 (100 SHS) CLOSING", "TRANSACTION"]}},
    ]
    income = [
        {"date": "03/26", "symbol": "ZQGG", "action": "Reinvestment", "qty": 4.6,
         "price": 88.20, "amount": -405.72, "trade_date": "03-25-26"},
        {"date": "03/26", "symbol": "ZQGG", "action": "Dividend Received",
         "qty": None, "price": None, "amount": 405.72},
        {"date": "03/31", "symbol": "SYNXX", "action": "Dividend Received",
         "qty": None, "price": None, "amount": 128.65},
    ]
    deposits = []
    withdrawals = [{"date": "03/09", "description": "Money Line Paid EFT FUNDS PAID ED55667788 /WEB",
                    "amount": -4_000.00, "note": "SYNTHETIC BANK NA ******1234"}]
    cash = [
        {"date": "03/05", "action": "You Bought", "amount": 1_239.34, "note": "@ 1"},
        {"date": "03/09", "action": "You Sold", "amount": -4_000.00, "note": "MORNING TRADE @ 1"},
        {"date": "03/17", "action": "You Sold", "amount": -1.05, "note": "MORNING TRADE @ 1"},
        {"date": "03/20", "action": "You Sold", "amount": -430.60, "note": "MORNING TRADE @ 1"},
        {"date": "03/26", "action": "Reinvestment", "amount": 405.72, "note": "REINVEST @ $1.000"},
        {"date": "03/31", "action": "Reinvestment", "amount": 128.65, "note": "REINVEST @ $1.000"},
        {"date": "03/31", "action": "You Bought", "amount": 11_050.28, "note": "@ 1"},
    ]
    merger = {"out": {"date": "03/12", "name": ["ZENITH FUSION HOLDINGS COM"], "cusip": "666666FF6",
                      "qty": -1_600.0, "amount": 1_832.00,
                      "note": ["*EXCHANGED FOR CUSIP 777777GG7 +",
                               "$1.145* MER PAYOUT",
                               "#REORCM0099887766000"]},
              "in": {"date": "03/12", "name": ["ZENITH GLOBAL MUNI FD INC"], "cusip": "777777GG7",
                     "qty": 1_960.0, "note": ["COM STK USD0.1 MER FROM 666666FF6",
                                              "#REOR M0099887766001"]}}
    pending = [{"trade": "03/31", "settle": "04/02", "name": ["ZENITH QUANTUM ALPHA ETF"],
                "symbol": "ZQAA", "action": "Bought", "qty": 25.0, "price": 54.90,
                "cost_basis": None, "amount": -1_372.50, "specific_share": True}]
    return dict(
        period=("March 1, 2026", "March 31, 2026"), page_label="Mar",
        begin_value=None, opening_core=41_318.51,
        holdings=holdings, trades=trades, income=income,
        deposits=deposits, withdrawals=withdrawals, cash=cash,
        masked_account_label=True, subtotal_inline=True,
        merger=merger, pending=pending, adjustments=False,
    )


MONTHS = [month_one, month_two, month_three]


def old_core_cash_statement():
    """An older core-only Fidelity statement layout. Its Core Account
    holding prints as bare CASH, with no parenthesized ticker line and
    no Total Cost Basis / Unrealized Gain/Loss columns. That pair of
    omissions is what fid-stmt.pdf and fid-stmt2.pdf exposed."""
    holdings = [
        old_core_cash(893.56, begin=9_823.76),
    ]
    income = [
        {"date": "01/31", "symbol": "CASH", "action": "Interest Earned",
         "qty": None, "price": None, "amount": 893.56},
    ]
    cash = [
        {"date": "01/15", "action": "You Bought", "amount": 1_666_666.00, "note": "CASH @ 1"},
        {"date": "01/17", "action": "You Sold", "amount": -1_098_046.76, "note": "CASH @ 1"},
        {"date": "01/31", "action": "You Sold", "zero_balance": True, "note": "CASH @ 1"},
        {"date": "01/31", "action": "Reinvestment", "amount": 893.56,
         "note": "CASH NET INT REINVEST"},
    ]
    return dict(
        period=("January 1, 2025", "January 31, 2025"), page_label="Jan",
        begin_value=7_983_655.56, opening_core=9_823.76,
        holdings=holdings, trades=[], income=income,
        deposits=[], withdrawals=[], cash=cash,
        masked_account_label=False, subtotal_inline=False,
        merger=None, pending=[], adjustments=False,
        old_core_cash_layout=True,
    )


OLDER_STATEMENTS = [old_core_cash_statement]


# --- layout ----------------------------------------------------------

class Page:
    """One page's text runs, as (x, y, string). Kept as runs rather than
    lines because the margin marker and the description it precedes are
    two runs whose *spacing* is the thing being reproduced."""

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
    def __init__(self, data, index, total_months):
        self.d = data
        self.index = index
        self.pages = []
        self.page = None
        self.section_title = "Holdings"

    # -- page plumbing --
    def new_page(self, section_title=None):
        if section_title:
            self.section_title = section_title
        self.page = Page()
        self.pages.append(self.page)
        p, d = self.page, self.d
        # The rotated mail-sort block, then the report header every page
        # repeats. The parser anchors its boilerplate strip on the period
        # line, so its placement here is load-bearing.
        y = SIDEBAR_TOP
        for code in ("S", "41062026", "BBBBB_SYNTHBBBB_", "EC_RM"):
            p.items.append((X_SIDEBAR, y, code))
            y -= LINE_H
        p.y = TOP
        p.line("INVESTMENT REPORT")
        p.line(f"{d['period'][0]} - {d['period'][1]}")
        if d.get("fully_masked_account"):
            # Every digit masked. Some months of the real statement run
            # are redacted this way, and the parser then has no account
            # identity to emit at all - see knownAccountNumber, which
            # resolves it against the account already on file rather
            # than letting the rows become a second, phantom account.
            p.line(f"Account # {'X' * (8 + len(ACCOUNT_DIGITS))}")
        elif d["masked_account_label"]:
            p.line(f"xxxxxxx x xxxxxx{ACCOUNT_DIGITS}")
        else:
            p.line(f"Account # XXXXXXXX{ACCOUNT_DIGITS}")
        p.line(self.section_title)
        p.line(REGISTRATION)
        return p

    def room(self, needed):
        return self.page.y - needed * LINE_H > 60

    def ensure(self, needed, section_title, continued_header=None):
        if self.room(needed):
            return
        self.new_page(section_title)
        if continued_header:
            continued_header()

    def finish(self):
        for i, p in enumerate(self.pages):
            p.at(X_FOOTER, f"{i + 1} of {len(self.pages)}")

    # -- holdings --
    def holdings_header(self, with_eai=True):
        p = self.page
        p.line("Beginning Price Ending Unrealized", x=225.6)
        tail = "Market Value Quantity Per Unit Market Value Total Gain/Loss"
        p.line(tail + (" EAI ($) /" if with_eai else ""), x=215.0)
        cols = "Description Jan 1, 2026 Jan 31, 2026 Jan 31, 2026 Jan 31, 2026 Cost Basis Jan 31, 2026"
        p.line(cols + (" EY (%)" if with_eai else ""))

    def old_core_cash_header(self):
        p = self.page
        p.line("Beginning Price Ending", x=225.6)
        p.line("Market Value Quantity Per Unit Market Value EAI ($) /", x=215.0)
        p.line("Description Jan 1, 2025 Jan 31, 2025 Jan 31, 2025 Jan 31, 2025 EY (%)")

    def emit_holding(self, h, with_eai=True):
        """One holdings row, plus the lines its name wraps onto.

        A margin position's "M" is emitted as its own run just left of
        the Description column, exactly as the statement prints it -
        extract_text() then glues it to the name with no separator,
        which is the case the parser has to resolve by x-coordinate."""
        p = self.page
        sec = SECURITIES.get(h.symbol, {})
        if h.option:
            first = f"{h.option['kind']} ({h.option['underlying']}) {h.option['name']}"
            wraps = list(h.option["text"])
            wraps[-1] = f"({h.option['occ']}) " + wraps[-1]
        else:
            names = list(sec.get("name", [h.symbol]))
            first, wraps = names[0], names[1:]
            wraps = wraps + [f"({h.symbol})"] if wraps else [f"({h.symbol})"]

        begin = "unavailable" if not h.begin_value else money(h.begin_value, dollar=True)
        cost = "not applicable" if h.cost_basis is None else money(h.cost_basis, dollar=True)
        gain = "not applicable" if h.cost_basis is None else money(h.gain, dollar=True)
        eai = money(h.eai, dollar=True) if h.eai else "-"
        cells = [begin, money(h.quantity, places=3), money(h.price, dollar=True, places=4),
                 money(h.value, dollar=True), cost, gain]
        if with_eai:
            cells.append(eai)
        if h.margin:
            p.at(X_GUTTER, "M")
        p.line(f"{first} " + " ".join(cells))
        for i, w in enumerate(wraps):
            last = i == len(wraps) - 1
            suffix = " 3.880%" if (last and with_eai and h.eai) else ""
            p.line(w + suffix, x=X_WRAP if i else X_DESC)

    def emit_old_core_cash_holding(self, h):
        p = self.page
        eai = money(h.eai, dollar=True) if h.eai else "-"
        p.line(
            f"CASH {money(h.begin_value, dollar=True)} {money(h.quantity, places=3)} "
            f"{money(h.price, dollar=True, places=4)} {money(h.value, dollar=True)} {eai}"
        )
        p.line("-")
        p.line("For balances below $99,999,999,999.99, the current interest rate is 2.19%.")

    def emit_holdings(self):
        d = self.d
        by_section = {}
        for h in d["holdings"]:
            sec = SECURITIES.get(h.symbol, {})
            name = "Options" if h.option else sec.get("section", "Stocks")
            sub = None if h.option else sec.get("sub")
            by_section.setdefault(name, {}).setdefault(sub, []).append(h)

        order = ["Core Account", "Exchange Traded Products", "Stocks", "Options"]
        p = self.new_page("Holdings")
        p.line("Holdings")
        for section in order:
            if section not in by_section:
                continue
            with_eai = section != "Options"     # the Options table is a column narrower
            self.ensure(8, "Holdings")
            p = self.page
            p.line(section)
            if section == "Exchange Traded Products":
                p.line("Includes exchange-traded funds (ETFs), exchange-traded notes (ETNs), "
                       "and other exchange-traded vehicles.")
            old_core = d.get("old_core_cash_layout") and section == "Core Account"
            if old_core:
                self.old_core_cash_header()
            else:
                self.holdings_header(with_eai)
            for sub, rows in by_section[section].items():
                if sub:
                    self.ensure(5, "Holdings", lambda: self.holdings_header(with_eai))
                    self.page.line(sub)
                for h in rows:
                    self.ensure(4, "Holdings", lambda: self.holdings_header(with_eai))
                    if old_core:
                        self.emit_old_core_cash_holding(h)
                    else:
                        self.emit_holding(h, with_eai)
                if sub:
                    self.emit_subtotal(f"Total {sub}", rows, with_eai)
            if section != "Core Account":
                self.emit_subtotal(f"Total {section}", 
                                   [h for rows in by_section[section].values() for h in rows],
                                   with_eai, always_inline=True)
            else:
                self.emit_subtotal("Total Core Account", by_section[section][None], with_eai,
                                   always_inline=True, core=True)

        every = d["holdings"]
        total = round(sum(h.value for h in every), 2)
        cost = round(sum(h.cost_basis for h in every if h.cost_basis is not None), 2)
        gain = round(sum(h.gain for h in every if h.cost_basis is not None), 2)
        eai = round(sum(h.eai for h in every if h.eai), 2)
        if d.get("old_core_cash_layout"):
            self.page.line(f"Total Holdings {money(total, dollar=True)} {money(cost, dollar=True)}")
        else:
            self.page.line(f"Total Holdings {money(total, dollar=True)} {money(cost, dollar=True)} "
                           f"{money(gain, dollar=True)} {money(eai, dollar=True)}")
        self.page.line("All remaining positions held in cash account.")
        self.page.line("M Position held in margin account.")
        self.total_holdings = total

    def emit_subtotal(self, label, rows, with_eai, always_inline=False, core=False):
        """Fidelity usually prints a sub-total's figures on the line
        above its label, and sometimes on the same line. Both shapes
        appear here because only the second one reads as a continuation
        of the holding above it."""
        p = self.page
        value = round(sum(h.value for h in rows), 2)
        cost = round(sum(h.cost_basis for h in rows if h.cost_basis is not None), 2)
        gain = round(sum(h.gain for h in rows if h.cost_basis is not None), 2)
        eai = round(sum(h.eai for h in rows if h.eai), 2)
        if core:
            figures = f"{money(value, dollar=True)} {money(eai, dollar=True)}"
        else:
            figures = (f"{money(value, dollar=True)} {money(cost, dollar=True)} "
                       f"{money(gain, dollar=True)}")
            if with_eai:
                figures += f" {money(eai, dollar=True)}"
        if always_inline:
            p.line(f"{label} (0% of account holdings) {figures}")
        elif self.d["subtotal_inline"]:
            p.line(f"{figures} {label} (0% of account holdings)", x=224.9)
        else:
            p.line(figures, x=225.8)
            p.line(f"{label} (0% of account holdings)")

    # -- activity --
    def trade_header(self):
        p = self.page
        p.line("Settlement Symbol/ Total Transaction")
        p.line("Date Security Name CUSIP Description Quantity Price Cost Basis Cost Amount")

    def income_header(self):
        p = self.page
        p.line("Settlement Symbol/")
        p.line("Date Security Name CUSIP Description Quantity Price Amount")

    def emit_activity(self):
        d = self.d
        self.new_page("Activity")
        p = self.page
        p.line("Activity")

        # --- Securities Bought & Sold ---
        p.line("Securities Bought & Sold")
        self.trade_header()
        bought = sold = 0.0
        for t in d["trades"]:
            self.ensure(5, "Activity", self.trade_header)
            p = self.page
            sec = SECURITIES[t["symbol"]]
            if t.get("option"):
                o = t["option"]
                head = f"{o['kind']} ({o['underlying']}) {o['name']}"
                wraps = list(o["text"])
            else:
                head, wraps = sec["name"][0], list(sec["name"][1:])
            # The dividend schedule Fidelity prints after the security's
            # name on a purchase made around a distribution. It is a
            # note about the trade rather than part of the name, and
            # leaving it attached is enough to stop the row matching the
            # same security in Holdings - which is the only way a
            # non-option activity row gets a ticker at all.
            wraps += list(t.get("schedule", ()))
            cost = "-" if t["cost_basis"] is None else money(t["cost_basis"])
            fee = "-" if t["fee"] is None else money(t["fee"])
            marker = "s" if t.get("specific_share") else ""
            # An option trade prints the CONTRACT's own identifier, not
            # the underlying's - on a real statement they never match
            # (AVGO's contracts carry 8651979BB, 8770859XV and so on
            # against the stock's own 11135F101). Printing the
            # underlying's here would give a contract and the stock it
            # is written on one shared CUSIP, which is the key a merger
            # is resolved through.
            cusip = t["option"].get("cusip", sec["cusip"]) if t.get("option") else sec["cusip"]
            p.line(f"{marker}{t['date']} {head} {cusip} {t['action']} "
                   f"{money(t['qty'], places=3)} {money(t['price'], dollar=True, places=5)} "
                   f"{cost} {fee} {money(t['amount'], dollar=True)}")
            # A realized-gain note prints inline with the wrapped
            # description on an option, and on its own line otherwise.
            note = t.get("gain_note")
            for i, w in enumerate(wraps):
                if note and t.get("option") and i == 0:
                    w = f"{w} {note}"
                    note = None
                p.line(w, x=72.0)
            if note:
                p.line(note, x=72.0)
            if t["amount"] < 0:
                bought += t["amount"]
            else:
                sold += t["amount"]
        fees = round(sum(t["fee"] for t in d["trades"] if t["fee"]), 2)
        p = self.page
        p.line(f"Total Securities Bought - {money(fees)} {money(round(bought, 2), dollar=True)}")
        p.line(f"Total Securities Sold - - {money(round(sold, 2), dollar=True)}")
        p.line(f"Net Securities Bought & Sold - {money(round(bought + sold, 2), dollar=True)}")
        self.bought, self.sold = round(bought, 2), round(sold, 2)

        # --- Dividends, Interest & Other Income ---
        self.ensure(8, "Activity")
        p = self.page
        p.line("Dividends, Interest & Other Income")
        p.line("(Includes dividend reinvestment)")
        self.income_header()
        income_total = 0.0
        for r in d["income"]:
            self.ensure(4, "Activity", self.income_header)
            p = self.page
            name = r.get("name") or SECURITIES[r["symbol"]]["name"]
            cusip = r.get("cusip") or SECURITIES[r["symbol"]]["cusip"]
            qty = money(r["qty"], places=3) if r["qty"] is not None else "-"
            price = money(r["price"], dollar=True, places=5) if r["price"] is not None else "-"
            p.line(f"{r['date']} {name[0]} {cusip} {r['action']} {qty} {price} "
                   f"{money(r['amount'], dollar=True)}")
            wraps = list(name[1:])
            td = r.get("trade_date")
            if td and r.get("split_trade_date"):
                # The annotation wraps mid-phrase, so neither line alone
                # contains "TRADE DATE".
                wraps.append("TRADE")
                wraps.append(f"DATE {td}")
            elif td:
                wraps.append(f"TRADE DATE {td}")
            for w in wraps:
                p.line(w, x=72.0)
            income_total += r["amount"]
        self.page.line("Total Dividends, Interest & Other Income "
                       f"{money(round(income_total, 2), dollar=True)}")
        self.income_total = round(income_total, 2)

        # --- Other Activity In / Out ---
        # The merger's outgoing leg here carries a cash-in-lieu payout in
        # the Amount column (a CVR/fractional buyout), the incoming leg
        # does not; adjustments never do.
        if d["merger"] or d["adjustments"]:
            self.ensure(10, "Activity")
            p = self.page
            for label, key in (("Other Activity In", "in"), ("Other Activity Out", "out")):
                p.line(label)
                self.trade_header()
                if d["merger"]:
                    m = d["merger"][key]
                    amt = money(m["amount"], dollar=True) if m.get("amount") else "-"
                    p.line(f"{m['date']} {m['name'][0]} {m['cusip']} Merger "
                           f"{money(m['qty'], places=3)} - - {amt}")
                    for w in m["name"][1:] + m["note"]:
                        p.line(w, x=72.0)
                if d["adjustments"]:
                    sign = -1 if key == "in" else 1
                    p.line(f"02/20 {'DECREASE' if key == 'in' else 'INCREASE'} COLLATERAL "
                           f"L0C990030 Adjustment {money(sign * 25_000.0, places=3)} - - -")
                p.line(f"Total {label} - -")
                p = self.page

        # --- Deposits / Withdrawals ---
        self.net_flows = round(sum(r["amount"] for r in d["deposits"] + d["withdrawals"]), 2)
        for label, rows in (("Deposits", d["deposits"]), ("Withdrawals", d["withdrawals"])):
            if not rows:
                continue
            self.ensure(6, "Activity")
            p = self.page
            p.line(label)
            p.line("Date Reference Description Amount")
            total = 0.0
            for r in rows:
                p.line(f"{r['date']} {r['description']} {money(r['amount'], dollar=True)}")
                if r.get("note"):
                    p.line(r["note"], x=72.0)
                total += r["amount"]
            p.line(f"Total {label} {money(round(total, 2), dollar=True)}")

        # --- Core Fund Activity ---
        self.ensure(8, "Activity")
        p = self.page
        p.line("Core Fund Activity")
        p.line("For more information about the operation of your core account, please refer to "
               "your Customer Agreement.")
        p.line("SettlementAccount")
        p.line("Date Type Transaction Description Quantity Price Amount Balance")
        balance = d["opening_core"]
        core_total = 0.0
        for r in d["cash"]:
            self.ensure(4, "Activity", lambda: (
                self.page.line("For more information about the operation of your core account, "
                               "please refer to your Customer Agreement."),
                self.page.line("SettlementAccount"),
                self.page.line("Date Type Transaction Description Quantity Price Amount Balance")))
            p = self.page
            balance = round(balance + r["amount"], 2)
            core_total += r["amount"]
            shown = "-" if r.get("zero_balance") else money(balance, dollar=True)
            p.line(f"{r['date']} CASH {r['action']} SYNTHETIC GOVERNMENT MONEY MARKET "
                   f"{money(r['amount'], places=3)} {money(1.0, dollar=True, places=4)} "
                   f"{money(r['amount'], dollar=True)} {shown}")
            p.line(r["note"], x=72.0)
        self.page.line(f"Total Core Fund Activity {money(round(core_total, 2), dollar=True)}")
        self.core_total = round(core_total, 2)
        self.closing_core = balance

        # --- Net Adjustments, then trades that settle next month ---
        self.ensure(6, "Activity")
        p = self.page
        p.line("Net Adjustments")
        self.trade_header()
        p.line("03/05 ZENITH QUANTUM BOND 222222BB2 Journaled 500.000 $100.8500 - -")
        p.line("INDEX ETF; YOU LOANED; XXXXXXX024-6;", x=72.0)
        p.line("VALUE OF TRANSACTION; $50,425.00", x=72.0)
        p.line("Total Net Adjustments -")

        if d["pending"]:
            self.ensure(8, "Activity")
            p = self.page
            p.line("Trades Pending Settlement")
            p.line("Trade Settlement Symbol/ Total")
            p.line("Date Date Security Name CUSIP Description Quantity Price Cost Basis Amount")
            total = 0.0
            for r in d["pending"]:
                marker = "s" if r.get("specific_share") else ""
                p.line(f"{marker}{r['trade']} {r['settle']} {r['name'][0]} {r['symbol']} "
                       f"{r['action']} {money(r['qty'], places=4)} "
                       f"{money(r['price'], dollar=True, places=5)} - "
                       f"{money(r['amount'], dollar=True)}")
                p.line("refer to confirm for Lot detail", x=72.0)
                total += r["amount"]
            p.line(f"Total Trades Pending Settlement {money(round(total, 2), dollar=True)}")

    # -- page 1 and the closing legal text --
    def emit_cover(self):
        """Page 1: the account title block the account *type* is read
        from, and the value summary that ties beginning value, external
        flows and investment change to the ending value."""
        p = Page()
        self.pages.insert(0, p)
        y = SIDEBAR_TOP
        for code in ("S", "41062026", "BBBBB_SYNTHBBBB_", "EC_RM"):
            p.items.append((X_SIDEBAR, y, code))
            y -= LINE_H
        p.line("INVESTMENT REPORT")
        p.line(f"{self.d['period'][0]} - {self.d['period'][1]}")
        p.line("Envelope # BSYNTHBBBBZZZ")
        p.line("XXXXX XXXXXXXX")
        p.line("XXXX XXXXX XX")
        p.blank()
        p.line("FIDELITY ACCOUNT XXXXX XXXXXXXX AND XXXXXX XXXXXXXX -")
        p.line("WITH RIGHTS OF SURVIVORSHIP TOD")
        if self.d.get("fully_masked_account"):
            p.line(f"Account Number: {'X' * (8 + len(ACCOUNT_DIGITS))}")
        else:
            p.line(f"Account Number: XXXXXXXX{ACCOUNT_DIGITS}")
        p.line(f"Your Account Value: {money(self.total_holdings, dollar=True)}")
        p.blank()
        deposits = round(sum(r["amount"] for r in self.d["deposits"]), 2)
        fees = round(sum(t["fee"] for t in self.d["trades"] if t["fee"]), 2)
        withdrawals = round(sum(r["amount"] for r in self.d["withdrawals"]), 2) + fees
        begin = self.d["begin_value"]
        p.line("This Period Year-to-Date")
        if begin is not None:
            change = round(self.total_holdings - begin - deposits - withdrawals, 2)
            p.line(f"Beginning Account Value {money(begin, dollar=True)} {money(begin, dollar=True)}")
            if deposits:
                p.line(f"Additions {money(deposits)} {money(deposits)}")
            if withdrawals:
                p.line(f"Subtractions {money(withdrawals)} {money(withdrawals)}")
            p.line(f"Change in Investment Value * {money(change)} {money(change)}")
        p.line(f"Ending Account Value ** {money(self.total_holdings, dollar=True)} "
               f"{money(self.total_holdings, dollar=True)}")
        p.line("Brokerage services provided by Synthetic Brokerage Services LLC, Member NYSE, SIPC.")

    def emit_endnotes(self):
        """The closing legal text. It names most retirement wrappers
        generically, which is exactly why the parser reads account type
        from the title block on page 1 and not from the document - a
        whole-document search would report an IRA for every statement
        ever printed, including this plainly taxable one."""
        p = self.new_page("Additional Information and Endnotes")
        p.line("Additional Information and Endnotes")
        p.line("Income Summary Shows income by tax status for the statement and year-to-date "
               "periods. In Traditional IRAs, Rollover IRAs, SEP-IRAs, SIMPLE")
        p.line("IRAs and Keoghs, earnings are reported as tax-deferred income. In Roth IRAs and "
               "HSAs, earnings are reported as tax-exempt income.")
        p.line("Cost basis and gain/loss information is provided as a service to our customers.")
        p.line("f FIFO (First-In, First-Out)")
        p.line("s Cost basis and gain/loss reporting for this security are based on Specific "
               "Share identification.")

    def prepare(self):
        """Ties the core position to the core ledger before anything is
        printed. The sweep account's closing balance *is* its market
        value, and the parser checks both - so deriving one from the
        other is what keeps the fixture internally consistent instead of
        needing two numbers kept in step by hand."""
        d = self.d
        # A row flagged zero_balance is the day the core account is
        # emptied, which the statement prints as "-" rather than $0.00.
        # Its amount has to be whatever takes the balance to exactly
        # zero, or the placeholder would contradict the running balance
        # the parser reconciles against.
        running = d["opening_core"]
        for r in d["cash"]:
            if r.get("zero_balance"):
                r["amount"] = round(-running, 2)
            running = round(running + r["amount"], 2)
        closing = running
        for h in d["holdings"]:
            if SECURITIES.get(h.symbol, {}).get("section") == "Core Account":
                h.quantity = closing
                break

    def expectations(self, filename):
        """What the statement says, in the shape a test can compare
        query results against. Emitted rather than transcribed so the
        fixtures and the assertions cannot drift apart: change a row
        here and the expected totals move with it."""
        positions = {}
        for h in self.d["holdings"]:
            symbol = h.option["occ"] if h.option else h.symbol
            key = symbol + ("/Margin" if h.margin else "")
            positions[key] = h.value
        return {
            "file": filename,
            "statementDate": self.close_date,
            "account": ACCOUNT_LAST4,
            "accountType": "Brokerage",
            "totalHoldings": self.total_holdings,
            "securitiesBought": self.bought,
            "securitiesSold": self.sold,
            "income": self.income_total,
            "coreFundActivity": self.core_total,
            "closingCoreBalance": self.closing_core,
            "netFlows": self.net_flows,
            "positions": positions,
        }

    def build(self):
        self.prepare()
        self.emit_holdings()
        self.emit_activity()
        self.emit_endnotes()
        self.emit_cover()
        self.finish()
        return self.pages


# --- PDF emission (same minimal writer the other gen-*.py scripts use) --

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
    # Each month opens where the last one closed, in both the core
    # ledger and the account value, so the three statements read as one
    # continuous account rather than three unrelated snapshots.
    carry_core = None
    carry_value = None
    expectations = []
    for factory in OLDER_STATEMENTS:
        data = factory()
        stmt = Statement(data, 0, 1)
        pages = stmt.build()
        close = data["period"][1]
        month, day, year = close.replace(",", "").split()
        num = {"January": "01"}[month]
        name = f"fidelity-synthetic-old-core-cash-{year}{num}.pdf"
        stmt.close_date = f"{year}-{num}-{int(day):02d}"
        build_pdf(pages, os.path.join(here, name))
        expectations.append(stmt.expectations(name))
        print(f"wrote {name}: {len(pages)} pages, holdings {money(stmt.total_holdings, dollar=True)}, "
              f"core closing {money(stmt.closing_core, dollar=True)}, "
              f"flows {money(stmt.net_flows, dollar=True)}, "
              f"income {money(stmt.income_total, dollar=True)}")

    for i, factory in enumerate(MONTHS):
        data = factory()
        if carry_core is not None:
            data["opening_core"] = carry_core
            data["begin_value"] = carry_value
        stmt = Statement(data, i, len(MONTHS))
        pages = stmt.build()
        close = data["period"][1]
        month, day, year = close.replace(",", "").split()
        num = {"January": "01", "February": "02", "March": "03"}[month]
        name = f"fidelity-synthetic-{year}{num}.pdf"
        stmt.close_date = f"{year}-{num}-{int(day):02d}"
        build_pdf(pages, os.path.join(here, name))
        expectations.append(stmt.expectations(name))
        carry_core, carry_value = stmt.closing_core, stmt.total_holdings
        print(f"wrote {name}: {len(pages)} pages, holdings {money(stmt.total_holdings, dollar=True)}, "
              f"core closing {money(stmt.closing_core, dollar=True)}, "
              f"flows {money(stmt.net_flows, dollar=True)}, "
              f"income {money(stmt.income_total, dollar=True)}")

    # A fully-masked month, kept out of the sidecar above on purpose:
    # the tests that sidecar drives chain each month's balances onto the
    # last, and this is January again rather than a fourth month. It
    # exists for one property - that a statement disclosing no account
    # number at all still lands on the right account - which
    # TestExtractFidelitySyntheticFullyMaskedAccount checks directly.
    data = month_one()
    data["fully_masked_account"] = True
    masked = Statement(data, 0, 1)
    masked_pages = masked.build()
    masked_name = "fidelity-synthetic-fully-masked-202601.pdf"
    build_pdf(masked_pages, os.path.join(here, masked_name))
    print(f"wrote {masked_name}: {len(masked_pages)} pages, every digit of the account number masked")

    sidecar = os.path.join(here, "fidelity-synthetic-expectations.json")
    with open(sidecar, "w") as f:
        json.dump({"statements": expectations}, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"wrote fidelity-synthetic-expectations.json ({len(expectations)} statements)")


if __name__ == "__main__":
    main()
