"""Fidelity Investments "INVESTMENT REPORT" brokerage statement parser.

Format: a monthly Fidelity brokerage statement, headed "INVESTMENT
REPORT" with a "<Month> D, YYYY - <Month> D, YYYY" period line. It has a
Holdings section (Core Account, Exchange Traded Products, Stocks,
Options - each optionally broken into sub-categories like "Equity
ETPs"/"Common Stock", and Options alone printed one column narrower)
and an Activity section broken into "Securities Bought & Sold",
"Dividends, Interest & Other Income", "Other Activity In"/"Out",
"Deposits"/"Withdrawals", "Core Fund Activity" (the money-market sweep
account, which is where this statement's cash actually lives) and
"Net Adjustments".

All three shared tables are populated from one parse() call:

  brokerage_holdings     <- Holdings (every section/sub-category)
  brokerage_transactions <- Securities Bought & Sold + Dividends,
                            Interest & Other Income + Deposits/Withdrawals
                            + Exchanges In/Out
                            + Transfers Between Fidelity Accounts

"Deposits" and "Withdrawals" are money crossing the account's boundary,
and they go to brokerage_transactions as Deposit/Withdrawal actions -
NOT to cash_transactions, even though they are cash. "Exchanges In" and
"Exchanges Out" are the cash side of Fidelity-account transfers, and
they go to brokerage_transactions with transaction_type
"internal_transfer" so the linker can offset the sending and receiving
accounts.

Core Fund Activity is different. It is the account's money-market sweep
ledger, not a separate economic event stream. It often accounts for the
same money Fidelity already printed elsewhere: external movements,
security buys/sells, and earned income all get swept through a $1 core
position. Emitting those rows would double-count income (e.g. "Interest
Earned CASH" plus "Reinvestment CASH NET INT REINVEST") and cash
movement. The parser still reads and verifies the section against
Fidelity's printed running balance and "Total Core Fund Activity", but
does not return it in any persisted table.

"Other Activity In"/"Other Activity Out" - merger/reorganization share
exchanges, option assignments and share-lending collateral adjustments -
are parsed into brokerage_transactions as corporate actions:
transaction_type "corporate_action", an `action` naming what happened
and which direction it went ("Merger In", "Merger Out", "Adjustment
Out"), the printed quantity, and the printed Transaction Amount.

Usually that amount is 0.00 - the "-" in the column, which is what a
pure share exchange moves. That is worth stating because these sections
used to be skipped on the grounds that a required numeric `amount` could
only be filled by inventing a figure - but the figure is not invented,
and skipping them cost the only record of why a position changed. When
the column DOES print a figure - a merger paying cash in lieu, a CVR
payout ("*EXCHANGED FOR CUSIP ... + $11.45* MER PAYOUT") - that cash is
real and is kept; it is not double-counted, because the money-market
sweep that mirrors it in Core Fund Activity is not persisted (below).
A merger is otherwise visible solely as 1,600 shares of one security
vanishing and 1,960 of another appearing, with nothing tying the two
together. related_security_id is that tie: the CUSIP on the other side
of the exchange, which the notes print on both legs (see
_collect_corporate_note). The Go side turns it into the securities
table's successor_symbol.

Two activity sections are still deliberately *not* parsed into any
table:

  * "Trades Pending Settlement" - trades executed inside this period
    that settle after it. Every other activity section is keyed on
    settlement date, so these same trades reappear in the next
    statement's "Securities Bought & Sold"; emitting them here as well
    would double-count them across two months.
  * "Net Adjustments" - journaled share-lending loans/returns and
    inter-sub-account auto-journals, likewise printed with "-" for
    Transaction Amount (and a "-" section total). Unlike a merger these
    do not change what the account owns - a loaned share is still the
    account's position - so there is no unexplained share count for them
    to explain.

pdfplumber's extract_text() linearizes each table row onto a single text
line, with the security's name sometimes wrapping onto one or more
following lines (which may also carry the ticker in parentheses, an
estimated-yield percentage, a "TRADE DATE MM-DD-YY" annotation or a
realized gain/loss note). Every regex below is written against that
extracted text - dumped with `go run . extract-statement <pdf> --dump`
and inspected first - not against the PDF's visual layout.

One thing the extracted text alone can't tell you: Fidelity flags a
position held in a margin account with a bare "M" in a narrow gutter to
the left of the Description column, and extract_text() glues it onto the
name with no separator ("MBROADCOM INC COM"). It is genuinely ambiguous
from the text - a cash-account holding of a security whose name starts
with M would look identical - so _margin_first_words() goes back to the
PDF (that's what pdf_path is for) and uses word x-coordinates to tell
the two apart: the gutter marker starts measurably left of the
"Description" column header. See that function for the details.

How much of a statement survives redaction varies, and none of the
parsing keys off text that a redactor might mask: the per-page header
strip anchors on the statement period line rather than the account line
(see _repeated_header_lines for why), and every activity date's year
comes from that same period line. Two redactions of the same account -
one keeping "Account # XXXXXXXXXX", one masking the label itself to
"xxxxxxx x xxxxxxNNNN" - parse identically.

Verified against six consecutive monthly statements for one account
(Jan, Feb, Apr-Jul 2026), which between them cover a plain month, an
options month, a merger, a securities-lending credit, a netted
deposit/withdrawal day, a zeroed core account, and two different
redaction styles.

Validated against the statement's own printed totals - see the four
_check_* functions, which raise rather than return quietly-wrong
numbers: sum of holdings' current_value against "Total Holdings", the
buy/sell totals against "Total Securities Bought"/"Total Securities
Sold", the income rows against "Total Dividends, Interest & Other
Income", the transfers against "Total Deposits"/"Total Withdrawals", and
the core-fund rows against both "Total Core Fund Activity" and their own
printed running balance.

Those line up with the account-value summary on page 1, which is not
asserted here only because it needs a figure from outside these tables:
beginning value + Additions + Subtractions + "Change in Investment
Value" = ending value, where Subtractions is this module's withdrawals
*plus* the per-trade transaction costs (July 2026: -$17,000.00 of
withdrawals and -$35.22 of fees against a printed -$17,035.22). Worth
knowing when reconciling by hand.
"""

import collections
import re
from datetime import date, datetime

import pdfplumber

import parser_common

from . import common

KIND = parser_common.KIND_BROKERAGE

_INSTITUTION = "Fidelity Investments"
# The type reported when the statement names no tax-advantaged wrapper -
# i.e. an ordinary taxable account, however it is registered. Individual,
# joint and trust registrations all map here on purpose: what matters for
# analysis is the tax treatment, and collapsing them keeps one account's
# type stable even when a redaction removes its registration line (this
# account's January statement prints "JOINT WROS - TOD" while later ones
# mask it away entirely).
_ACCOUNT_TYPE = "Brokerage"

# Retirement/tax-advantaged wrappers, longest-first so "ROTH IRA" is not
# claimed by the bare "IRA" pattern below it. Matched only against the
# statement's own account title block - never the whole document, whose
# closing legal text names most of these generically ("In Traditional
# IRAs, Rollover IRAs, SEP-IRAs, SIMPLE IRAs and Keoghs, earnings
# are...") and would otherwise match every statement.
# The account-type vocabulary now lives once in common.ACCOUNT_TYPE_PATTERNS
# (see common.classify_account_type). It used to be duplicated here and in
# vanguard_brokerage.py, and the two copies had drifted - this one knew
# about HSA and 529 and Vanguard's did not.

# The account title block on page 1 - "FIDELITY ACCOUNT <registration>"
# down to the account number - which is where the wrapper is named.
_ACCOUNT_TITLE_RE = re.compile(r"^(?:FIDELITY|NETBENEFITS)\b.*ACCOUNT\b", re.I)

# Mask characters a redaction leaves behind in an account number. They
# carry no information, so they are dropped rather than treated as part
# of the identifier.
_MASK_CHARS_RE = re.compile(r"[Xx*#\u2022]+")
_CURRENCY = "USD"
_EPS = 0.01

_REPORT_MARKER = "INVESTMENT REPORT"

# --- header/boilerplate ---

_PERIOD_RE = re.compile(
    r"([A-Z][a-z]+ \d{1,2}, \d{4})\s*-\s*([A-Z][a-z]+ \d{1,2}, \d{4})"
)
# Page 1 prints the full "Account Number: ..."; every later page repeats
# a short "Account # ..." in its header block.
_ACCOUNT_NUMBER_RE = re.compile(r"Account Number:\s*(\S+)")
# Page 1 prints the full "Account Number: ...". Later pages repeat a
# short account line in their header block, but its label does not
# reliably survive redaction (one sample keeps "Account # XXXXXXXXXX",
# another masks the label itself down to "xxxxxxx x xxxxxxNNNN"), so
# nothing keys off it - see _repeated_header_lines.
_ACCOUNT_ANY_RE = re.compile(r"Account (?:Number:|#)\s*(\S+)")
# The statement period, which every page reprints directly above its
# account line. Unlike the account/registration lines it is never PII,
# so it survives redaction intact and makes a stable header anchor.
_PERIOD_LINE_RE = re.compile(r"^[A-Z][a-z]+ \d{1,2}, \d{4}\s*-\s*[A-Z][a-z]+ \d{1,2}, \d{4}$")
# How many lines after the period line the repeated header can occupy
# (account line, page title, account registration).
_HEADER_LINES = 3
_PAGE_FOOTER_RE = re.compile(r"^\d+ of \d+$")

# Page titles printed in the same header block as the account line and
# the account registration; excluded when looking for repeated header
# lines (see _repeated_header_lines) and dropped wherever they appear.
_PAGE_TITLES = frozenset({
    "Account Summary",
    "Holdings",
    "Activity",
    "Estimated Cash Flow",
    "Additional Information and Endnotes",
    "Information About Your Fidelity Statement",
})

# --- shared numeric token shapes ---

# A money/quantity/price figure as printed: optional sign and/or '$' in
# either order ("-$100.02" and "$-100.02" both occur across Fidelity
# sections), thousands separators, always a decimal point - requiring
# the point is what keeps a bare number inside a security name (the
# "500" in "NEOS S&P 500 HI", the "0-3" in "ISHARES TR 0-3 MNTH
# TREASRY") from being mistaken for a column value.
_NUM = r"-?\$?-?[\d,]+\.\d+"
# A holdings cell, which may also be one of Fidelity's placeholders
# instead of a figure. "--" must precede "-" in the alternation.
_VALUE = r"(?:not applicable|unavailable|--|-|" + _NUM + r"%?)"
_PLACEHOLDERS = frozenset({"", "-", "--", "not applicable", "unavailable"})


def _amount(token: str | None) -> float | None:
    """Parses one printed cell into a float, or None for any of
    Fidelity's "no value here" placeholders. Unlike common.parse_amount
    this also tolerates the 'f' (FIFO) marker suffixed to a cost-basis
    figure and the '%' suffixed to an estimated yield.
    """
    if token is None:
        return None
    t = token.strip().rstrip("f").rstrip("%")
    if t in _PLACEHOLDERS:
        return None
    return common.parse_amount(t)


def _norm_key(name: str) -> str:
    """Collapses a security name to a comparison key, so the same
    security matches across sections despite spacing/punctuation drift
    in the extracted text ("NEOS S&P 500 HI" in Holdings vs "NEOS S&P
    500 H I" in Activity).
    """
    return re.sub(r"[^A-Z0-9]", "", name.upper())


# --- Holdings ---

_HOLDING_SECTIONS = frozenset({
    "Core Account",
    "Exchange Traded Products",
    "Stocks",
    "Mutual Funds",
    "Bonds",
    "Options",
    "Other Investments",
})
# Sub-category labels within a holdings section. An unrecognized one in
# some future statement costs only the row's optional "subtype".
_HOLDING_SUBSECTIONS = frozenset({
    "Equity ETPs",
    "Fixed Income ETPs",
    "Commodity ETPs",
    "Other ETPs",
    "Common Stock",
    "Preferred Stock",
})

_HOLDING_ROW_RE = re.compile(
    r"^(?P<description>\S.*?)"
    r"\s+(?P<beginning>" + _VALUE + r")"
    r"\s+(?P<quantity>" + _NUM + r")"
    r"\s+(?P<price>" + _NUM + r")"
    r"\s+(?P<value>" + _NUM + r")"
    # Some older/core-only statements omit the Total Cost Basis and
    # Unrealized Gain/Loss columns entirely, leaving only EAI/EY after
    # Ending Market Value.
    r"(?:\s+(?P<cost>" + _VALUE + r")"
    r"\s+(?P<gain>" + _VALUE + r"))?"
    # The Options table drops the EAI ($) / EY (%) column every other
    # holdings table carries, so the last cell is optional. The lazy
    # description means the engine still prefers the full seven-column
    # split wherever one exists.
    r"(?:\s+(?P<eai>" + _VALUE + r"))?$"
)
# A year-end investment report prints its Holdings tables without the
# leading "Beginning Market Value" (Jan 1) column every monthly/quarterly
# statement carries: its columns are Quantity, Price Per Unit, Total
# Market Value, Total Cost Basis, Total Unrealized Gain/Loss, Income
# Earned. Same row shape otherwise, one value cell shorter - read with
# the wrong regex, "Total Cost Basis" lands in current_value and the
# parsed holdings total comes out as the statement's cost-basis total.
_HOLDING_ROW_RE_YEAR_END = re.compile(
    r"^(?P<description>\S.*?)"
    r"\s+(?P<quantity>" + _NUM + r")"
    r"\s+(?P<price>" + _NUM + r")"
    r"\s+(?P<value>" + _NUM + r")"
    r"(?:\s+(?P<cost>" + _VALUE + r")"
    r"\s+(?P<gain>" + _VALUE + r"))?"
    # "Income Earned" - trailing-period income, not a forward EAI
    # estimate, so it is matched-and-dropped, never stored.
    r"(?:\s+(?P<eai>" + _VALUE + r"))?$"
)
# The Holdings column header that names a year-end report's layout. The
# cover page's "YEAR-END INVESTMENT REPORT" title is redaction-safe too,
# but this is the line that actually describes the table being parsed.
_YEAR_END_HOLDINGS_HEADER_RE = re.compile(
    r"^Description\s+Quantity\s+Per Unit\s+Market Value\s+Cost Basis\b"
)
_TICKER_RE = re.compile(r"\(([A-Z]{1,6})\)")
# An OCC-style option contract code, e.g. "(AVGO260731C400)" or
# "(CRWV260821P82.5)". Preferred over the underlying's plain ticker,
# which is printed alongside it ("CALL (AVGO) BROADCOM INC COM ...") and
# would otherwise collide with the underlying stock's own holdings row.
_OPTION_SYMBOL_RE = re.compile(r"\(([A-Z]{1,6}\d{6}[CP][\d.]+)\)")
# The same contract as an Activity row describes it, which is the only
# form it comes in there: Activity never prints the OCC code that
# Holdings prints, only the contract in words -
#
#     You Sold CALL (AVGO) BROADCOM INC COM MAY 29 26 $430 (100 SHS) OPENING TRANSACTION
#
# Every part of the code is in that line, so it can be rebuilt rather
# than looked up - which matters because looking it up cannot work for
# the contracts that matter most. _finish_activity resolves a security
# by matching its name against the Holdings section, and a contract
# opened and closed inside the same period is never in Holdings. Those
# are exactly the rows that realize a gain: in one real project 29 such
# rows carried $34,784.55 of realized gain, more than the account's
# entire net, and every one of them landed under an empty symbol,
# joinable to no security and countable in no breakdown by asset class.
_OPTION_DESC_RE = re.compile(
    r"\b(?P<kind>CALL|PUT)\s*\((?P<underlying>[A-Z]{1,6})\)"
    r".*?\b(?P<month>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+"
    r"(?P<day>\d{1,2})\s+(?P<year>\d{2})\s+\$(?P<strike>[\d,]+(?:\.\d+)?)")
_OPTION_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_VALUE_ONLY_RE = re.compile(r"^(?:" + _VALUE + r")$")
# A sub-total's figures usually sit on the line above its "Total ..."
# label, but not always - Fidelity sometimes prints both on one line
# ("$139,491.27 355,892.24 ... Total Equity ETPs (9% of account
# holdings)"). Stripping the leading figures is what lets the section
# walker recognize such a line as the total it is, rather than appending
# it to the description of the holding above.
_LEADING_VALUES_RE = re.compile(r"^(?:" + _VALUE + r"\s+)+")
# The estimated yield, which Fidelity prints trailing the security's
# name on a wrapped line rather than in the EY column.
#
# It must be preceded by a space or start the line, or the figure inside
# a name that ends in one gets eaten: "COM STK USD0.1" became "COM STK
# USD", which is not what the Activity sections print for the same
# security, so every trade and dividend row for it failed to resolve to
# a ticker (see _finish_activity, which matches the two by name).
_TRAILING_YIELD_RE = re.compile(r"(?:^|\s)(?:" + _NUM + r"%?|--|-)$")

# --- Activity ---

_ACTIVITY_SECTIONS = {
    "Securities Bought & Sold": "trades",
    "Dividends, Interest & Other Income": "income",
    "Core Fund Activity": "core",
    "Deposits": "transfers",
    "Withdrawals": "transfers",
    "Exchanges In": "exchanges",
    "Exchanges Out": "exchanges",
    "Transfers Between Fidelity Accounts": "internal_transfers",
    "Trades Pending Settlement": "skip",
    "Other Activity In": "corporate",
    "Other Activity Out": "corporate",
    "Net Adjustments": "skip",
}

# Fidelity flags a row whose cost basis uses specific-share
# identification with a lowercase "s" in a gutter left of the date
# column, and extract_text() glues it on ("s07/27"). Unlike the margin
# marker on a holdings description this needs no x-coordinate check to
# resolve: the date that follows is rigidly formatted, so anything in
# front of it is a marker.
_ROW_MARKER = r"[a-z]?"

_TRADE_ROW_RE = re.compile(
    r"^" + _ROW_MARKER + r"(?P<date>\d{2}/\d{2})"
    r"\s+(?P<name>\S.*?)"
    r"\s+(?P<security_id>[0-9A-Z]{9})"
    r"\s+(?P<label>You Bought|You Sold)"
    r"\s+(?P<quantity>" + _NUM + r")"
    r"\s+(?P<price>" + _NUM + r")"
    r"\s+(?P<rest>\S.*)$"
)
# Longest-first: "Interest Earned" must precede the bare "Interest"
# Fidelity prints on a fully-paid securities-lending row, or the
# alternation would match the prefix and strand " Earned".
_INCOME_LABELS = (
    "Reinvestment|Dividend Received|Interest Earned|Interest|"
    "Long-term Cap Gain|Short-term Cap Gain|Return Of Capital"
)
_INCOME_ROW_RE = re.compile(
    r"^" + _ROW_MARKER + r"(?P<date>\d{2}/\d{2})"
    r"\s+(?P<name>\S.*?)"
    r"\s+(?P<security_id>[0-9A-Z]{9})"
    r"\s+(?P<label>" + _INCOME_LABELS + r")"
    r"\s+(?P<quantity>" + _NUM + r"|-)"
    r"\s+(?P<price>" + _NUM + r"|-)"
    r"\s+(?P<amount>" + _NUM + r")$"
)
# A Deposits/Withdrawals row: date, an optional reference, a free-text
# description and the amount, signed as printed (in for a deposit, out
# for a withdrawal).
_TRANSFER_ROW_RE = re.compile(
    r"^" + _ROW_MARKER + r"(?P<date>\d{2}/\d{2})"
    r"\s+(?P<description>\S.*?)"
    r"\s+(?P<amount>" + _NUM + r")$"
)
_EXCHANGE_ROW_RE = re.compile(
    r"^" + _ROW_MARKER + r"(?P<date>\d{2}/\d{2})"
    r"\s+(?P<reference>\S.*?)"
    r"\s+(?P<label>Transferred To|Transferred From)"
    r"\s+-\s+-"
    r"\s+(?P<amount>" + _NUM + r")$"
)
_INTERNAL_TRANSFER_REF = r"(?:[A-Z]\d{2}-\d{6}-\d|X{6,}\s*\d?\s*-?\s*\d)"
_INTERNAL_TRANSFER_ROW_RE = re.compile(
    r"^" + _ROW_MARKER + r"(?P<date>\d{2}/\d{2})"
    r"\s+(?P<name>\S.*?)"
    r"(?:\s+(?P<reference>" + _INTERNAL_TRANSFER_REF + r")\s*)?"
    r"(?P<security_id>[0-9A-Z]{9})"
    r"\s+(?P<label>Transferred To|Transferred From)"
    r"\s+(?P<quantity>" + _NUM + r")"
    r"\s+(?P<price>" + _NUM + r")"
    r"\s+\S.*$"
)
_INTERNAL_TRANSFER_REF_RE = re.compile(r"\b" + _INTERNAL_TRANSFER_REF + r"\b")
_INTERNAL_TRANSFER_VALUE_RE = re.compile(
    r"\bVALUE OF TRANSACTION\b\s*\$?(?P<amount>[\d,]+\.\d+)", re.I)
_INTERNAL_TRANSFER_VALUE_LABEL_RE = re.compile(r"\bVALUE OF(?:\s+TRANSACTION)?\b.*$", re.I)
_BARE_MONEY_RE = re.compile(r"^\$?(?P<amount>[\d,]+\.\d+)$")
_TRANSFER_VALUE_CONTINUATION_RE = re.compile(r"^TRANSACTION\s+\$?(?P<amount>[\d,]+\.\d+)$", re.I)
# --- Other Activity In / Out ---
#
# Share movements with no cash behind them: a merger exchanging one
# security for another, an option assignment, a share-lending collateral
# adjustment. Same seven-column shape as a trade row, but with "-" in
# every money column - which is why the quantity is the last thing this
# has to match.
_CORPORATE_ROW_RE = re.compile(
    r"^" + _ROW_MARKER + r"(?P<date>\d{2}/\d{2})"
    r"\s+(?P<name>\S.*?)"
    r"\s+(?P<security_id>[0-9A-Z]{9})"
    r"\s+(?P<label>[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})"
    r"\s+(?P<quantity>" + _NUM + r")"
    r"(?P<values>(?:\s+" + _VALUE + r")*)\s*$"
)
# The successor/predecessor CUSIP, which the notes under a merger row
# print in one of two directions: the incoming leg says what it came
# from, the outgoing leg says what it went to.
_MERGER_FROM_RE = re.compile(r"\bMER FROM\s+(?P<cusip>[0-9A-Z]{9})\b")
_MERGER_TO_RE = re.compile(r"\bCUSIP\s+(?P<cusip>[0-9A-Z]{9})\b")
# The reorganization reference. It follows "#REOR" on the same line
# when there is room for it and wraps onto the next line when there is
# not, so both shapes have to be read - a wrapped one left unread ends
# up appended to the security's name, which is what the name is matched
# against to find its ticker.
_REOR_REF_RE = re.compile(r"#REOR\s*(?P<reference>[0-9A-Z]{6,})\b")
_REOR_WRAPPED_RE = re.compile(r"#REOR\s*$")
_REOR_CONTINUATION_RE = re.compile(r"^(?P<reference>[0-9A-Z]{6,})\b")
# Everything from the first note marker to the end of the line is
# annotation rather than more of the security's name. Anchored on the
# markers themselves so a name that wraps ("COM STK USD0.1 MER FROM
# 666666FF6") keeps the part that is really the name.
# "#REOR" gets no leading \b - the boundary would have to fall between
# the start of the line and "#", and neither is a word character, so it
# never matches and the whole reference line survives into the name.
_CORPORATE_NOTE_RE = re.compile(
    r"(?:\bMER FROM\b|\bMER PAYOUT\b|\bEXCHANGED FOR\b|\bCUSIP\b|#REOR).*$"
)
# Direction suffixes. They are part of `action` and not only of
# `subtype` because brokerage_transactions dedups on
# (institution, account, date, symbol, action, amount) and every row
# here has amount 0.00 - without the direction, the "DECREASE
# COLLATERAL" row under Other Activity In and the "INCREASE COLLATERAL"
# one under Other Activity Out collide on that key and one is lost.
_CORPORATE_DIRECTIONS = {"Other Activity In": "In", "Other Activity Out": "Out"}
# Marks these rows as something other than a trade, so a query for what
# was bought and sold can exclude them in one predicate.
_CORPORATE_TYPE = "corporate_action"

_TOTAL_DEPOSITS_RE = re.compile(r"^Total Deposits\s+(" + _NUM + r")\s*$", re.M)
_TOTAL_WITHDRAWALS_RE = re.compile(r"^Total Withdrawals\s+(" + _NUM + r")\s*$", re.M)
_TOTAL_EXCHANGES_IN_RE = re.compile(r"^Total Exchanges In\s+(" + _NUM + r")\s*$", re.M)
_TOTAL_EXCHANGES_OUT_RE = re.compile(r"^Total Exchanges Out\s+(" + _NUM + r")\s*$", re.M)

_CORE_ROW_RE = re.compile(
    r"^" + _ROW_MARKER + r"(?P<date>\d{2}/\d{2})"
    r"\s+(?P<account_type>[A-Z]+)"
    r"\s+(?P<description>\S.*?)"
    r"\s+(?P<quantity>" + _NUM + r")"
    r"\s+(?P<price>" + _NUM + r")"
    r"\s+(?P<amount>" + _NUM + r")"
    # A zero closing balance is printed as "-" rather than $0.00, e.g.
    # the row that empties the core account before it is refunded.
    r"\s+(?P<balance>" + _NUM + r"|--|-)$"
)

_ACTIONS = {
    "You Bought": "Buy",
    "You Sold": "Sell",
    "Dividend Received": "Dividend",
    "Reinvestment": "Reinvestment",
    "Interest Earned": "Interest",
    "Interest": "Interest",
    "Long-term Cap Gain": "Capital Gain",
    "Short-term Cap Gain": "Capital Gain",
    "Return Of Capital": "Return Of Capital",
}
# Money crossing the account boundary, as opposed to moving within it.
_DEPOSIT_ACTION = "Deposit"
_WITHDRAWAL_ACTION = "Withdrawal"
_INTERNAL_TRANSFER_TYPE = "internal_transfer"
_CORE_FUND_ACTIVITY_TYPE = "core_fund_activity"
_TRANSFER_IN_ACTION = "Transfer In"
_TRANSFER_OUT_ACTION = "Transfer Out"

# Continuation lines under an activity row that annotate the trade
# rather than continuing the security's name.
_TRADE_NOTE_RE = re.compile(r"^(?:Short-term|Long-term|Wash sale|Adjusted due to)\b")
# The same realized gain/loss and confirm-reference annotations, which on
# an option trade are printed *inline* with the contract's own wrapped
# description rather than on their own line ("JUL 24 26 $960 (100 SHS)
# CLOSING Short-term gain: $3,531.60"), so they have to be cut out of a
# continuation line rather than used to reject one.
_ACTIVITY_NOTE_RE = re.compile(
    # The settlement-vs-trade date note, which wraps mid-phrase often
    # enough to be worth matching only once the name is assembled
    # ("... HIGH INCOME TRADE" / "DATE 05-21-26"), and which sometimes
    # sits inline *before* the CUSIP on the row's own first line
    # ("BROADCOM INC COM TRADE DATE 11135F101 Reinvestment ...").
    r"\bTRADE DATE\b(?:\s+\d{2}-\d{2}-\d{2})?"
    # Realized gain/loss notes. The figure is optional because it is
    # frequently pushed onto the following line on its own.
    r"|(?:Short-term|Long-term)\s+(?:disallowed\s+)?(?:gain|loss)\s*:(?:\s*\$?[\d,]+\.\d+)?"
    r"|\bWash sale of\s*:(?:\s*\d{2}/\d{2}/\d{4})?"
    r"|\brefer to confirm for Lot detail\b"
    # The dividend schedule Fidelity prints after the security's name on
    # a purchase made around a distribution ("NEOS ETF TRUST NEOS ENH
    # INC 1-3 EX-DIV DATE 05/13/26 RECORD DATE 05/13/26 PAYABLE DTE
    # 05/15/26"). It is a note about the trade, not part of the name,
    # and leaving it attached is enough to stop the name matching the
    # identical one in Holdings - which is the only way a non-option
    # activity row gets a ticker at all.
    r"|\b(?:EX-DIV DATE|RECORD DATE|PAYABLE DTE)\b(?:\s+\d{2}/\d{2}/\d{2,4})?"
)
_BARE_NUM_RE = re.compile(r"^" + _NUM + r"$")
# The realized gain/loss a sale reports, printed as notes under the row.
# A single sale can print several: a gain and a loss against different
# tax lots, plus a wash-sale disallowed loss, which is added back rather
# than subtracted (it is a loss the IRS does not let you take yet).
_REALIZED_RE = re.compile(
    r"\b(?P<term>Short-term|Long-term)\s+(?P<disallowed>disallowed\s+)?(?P<kind>gain|loss)"
    r"\s*:\s*\$?(?P<amount>[\d,]+\.\d+)", re.I)
# The disallowed-loss note is the one that regularly wraps, printing its
# label with the figure pushed onto the next line. Left unhandled the
# add-back is silently dropped.
_DISALLOWED_LABEL_RE = re.compile(
    r"\b(Short-term|Long-term)\s+disallowed\s+loss\s*:\s*$", re.I)
_BARE_TRADE_DATE_RE = re.compile(r"^\d{2}-\d{2}-\d{2}$")

# --- printed section totals, used only to verify what was parsed ---

_TOTAL_HOLDINGS_RE = re.compile(r"^Total Holdings\s+(" + _NUM + r")", re.M)
_TOTAL_BOUGHT_RE = re.compile(r"^Total Securities Bought\b.*?(" + _NUM + r")\s*$", re.M)
_TOTAL_SOLD_RE = re.compile(r"^Total Securities Sold\b.*?(" + _NUM + r")\s*$", re.M)
_TOTAL_INCOME_RE = re.compile(
    r"^Total Dividends, Interest & Other Income\s+(" + _NUM + r")\s*$", re.M
)
_TOTAL_CORE_RE = re.compile(r"^Total Core Fund Activity\s+(" + _NUM + r")\s*$", re.M)


def detect(head_text: str) -> tuple[bool, str]:
    if _REPORT_MARKER not in head_text:
        return False, f"{_REPORT_MARKER!r} not found"
    if "fidelity" not in head_text.lower():
        return False, f"{_REPORT_MARKER!r} found but 'Fidelity' not found"
    return True, f"found {_REPORT_MARKER!r} and 'Fidelity'"


def _margin_first_words(pdf_path: str) -> set[str]:
    """Returns the set of glued first words ("MBROADCOM", "MNEOS", ...)
    that actually begin with Fidelity's margin-account marker.

    Fidelity prints a bare "M" in a gutter left of the Description
    column against every holding held in a margin account, and explains
    it in a legend ("M Position held in margin account."). Because the
    gutter is narrow, extract_text() renders the marker glued to the
    name with no separator, and the result is indistinguishable from a
    cash-account holding of a security whose name genuinely starts with
    M - stripping a leading "M" unconditionally would turn a real
    "MICROSOFT CORP" into "ICROSOFT CORP".

    extract_words() does keep the x-coordinates, so the two cases are
    easy to separate there: a marked row's first word starts at the
    gutter, several points left of the "Description" column header that
    every unmarked row lines up with. We match on the exact glued word,
    so the caller can simply ask "is this row's first token one of
    these?" without needing to line text lines up with word positions.

    Degrades to an empty set (i.e. nothing is treated as marked, and
    every description keeps whatever the extracted text said) if the
    PDF can't be re-read or no "Description" column header is found.
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages = [page.extract_words() for page in pdf.pages]
    except Exception:  # noqa: BLE001 - margin marking is a nicety; never fail the parse over it
        return set()

    first_words = []
    description_x0 = []
    for words in pages:
        rows = collections.defaultdict(list)
        for word in words:
            rows[round(word["top"])].append(word)
        for top in rows:
            first = min(rows[top], key=lambda w: w["x0"])
            if first["text"] == "Description":
                description_x0.append(first["x0"])
            first_words.append((first["text"], first["x0"]))

    if not description_x0:
        return set()
    # Every holdings table repeats the same column header, so the modal
    # x0 is the description column's left edge even if some statement
    # has a stray "Description" elsewhere.
    column_x0 = collections.Counter(round(x, 1) for x in description_x0).most_common(1)[0][0]
    return {
        text
        for text, x0 in first_words
        if text.startswith("M") and len(text) > 1 and x0 < column_x0 - 2.0
    }


def _statement_period(text: str) -> tuple[date | None, date | None]:
    m = _PERIOD_RE.search(text)
    if not m:
        return None, None
    try:
        start = datetime.strptime(m.group(1), "%B %d, %Y").date()
        end = datetime.strptime(m.group(2), "%B %d, %Y").date()
    except ValueError:
        return None, None
    return start, end


def _resolve_date(month_day: str, start: date | None, end: date | None) -> str:
    """Turns an "MM/DD" activity date into YYYY-MM-DD. Activity rows
    print no year, so it comes from the statement period - picking
    whichever of the period's two years puts the date inside the period
    handles a statement that straddles a year boundary.
    """
    month, day = (int(part) for part in month_day.split("/"))
    if start is None or end is None:
        raise ValueError(f"cannot resolve year for activity date {month_day}: no statement period")

    # A row's date is not always inside the period: a settlement-dated
    # section routinely carries items from the last day or two of the
    # previous month (a Feb statement here lists two 01/30 dividends).
    # So rather than requiring the date to land in the period, take the
    # year that puts it *closest* to the period - which for a January
    # statement's 12/30 row is the year before, not the statement's own.
    candidates = []
    for year in (start.year - 1, start.year, end.year, end.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:  # 02/29 in a non-leap year
            continue
        if start <= candidate <= end:
            return candidate.isoformat()
        distance = (start - candidate).days if candidate < start else (candidate - end).days
        candidates.append((distance, candidate))
    if not candidates:
        raise ValueError(f"activity date {month_day} is not a real date in any year of the statement period")
    return min(candidates)[1].isoformat()


def _base_label(line: str) -> str:
    """Strips the " (continued)" a section/sub-section header carries
    where its table runs across a page break."""
    suffix = " (continued)"
    return line[: -len(suffix)] if line.endswith(suffix) else line


def _is_structural(line: str) -> bool:
    """True for a line that names a section, sub-section, page title or
    total - i.e. one the section walker steers by, which must never be
    mistaken for repeated header boilerplate."""
    base = _base_label(line)
    return (
        base in _PAGE_TITLES
        or base in _HOLDING_SECTIONS
        or base in _ACTIVITY_SECTIONS
        or base in _HOLDING_SUBSECTIONS
        or line.startswith("Total ")
        or line.startswith("Net Securities Bought")
    )


def _is_bare_figures(line: str) -> bool:
    """True for a line that is nothing but column figures/placeholders.

    A sub-total whose label is too long to share a line with its own
    numbers gets split across two lines, figures first ("$218,342.24
    352,784.31 356,081.56 -3,297.25 46,482.87" / "Total Equity ETPs (8%
    of account holdings)"). That figures line is not a holding: it has no
    description, and its cells are one column to the left of a holding
    row's because the missing description shifts everything over - so
    read as one it books the section's *estimated annual income* as a
    position's market value, and the parsed holdings total then overshoots
    "Total Holdings" by the EAI of every sub-category printed this way.

    It has to be tested before _HOLDING_ROW_RE rather than left to
    _extend_holding, because the row regex matches such a line whenever
    the optional Cost Basis/Gain-Loss pair is absent - five cells is both
    "description + the four columns an older statement prints" and "the
    five figures a sub-total wraps onto its own line".
    """
    tokens = line.split()
    return len(tokens) >= 3 and all(_VALUE_ONLY_RE.match(token) for token in tokens)


def _repeated_header_lines(bodies: list[list[str]]) -> set[str]:
    """Finds the account line and the account registration line, which
    every page reprints directly under the statement period.

    Both are statement-specific text, and how much of them survives
    redaction varies between samples - one keeps the literal "Account #"
    label and masks only the digits, another masks the label itself down
    to "xxxxxxx x xxxxxxNNNN". So neither is matched by pattern: a line
    printed in the header slot on more than one page is header, not
    content. Section markers are excluded, because a table running
    across a page break legitimately reprints its own header there.
    """
    counts = collections.Counter()
    for lines in bodies:
        counts.update({
            line for line in lines[:_HEADER_LINES] if line and not _is_structural(line)
        })
    return {line for line, count in counts.items() if count > 1}


def _clean_pages(pages_text: list[str]) -> list[str]:
    """Strips the block every page repeats - the mail-sort/envelope
    codes, "INVESTMENT REPORT", the statement period, the account line,
    the page title and the account registration - plus the "N of M"
    footer, so the section walker below only ever sees content lines
    regardless of which page they came from (sections routinely run
    across a page break).

    The block is identified structurally rather than by matching its
    text: everything up to and including the period line goes, and the
    account/registration lines just after it are found by repetition
    (see _repeated_header_lines).

    Returns (content lines, repeated header lines) - the latter because
    the account line is one of them, and _account_number reads it.
    """
    bodies = []
    for page in pages_text:
        lines = [line.strip() for line in page.splitlines()]
        start = 0
        for i, line in enumerate(lines):
            if _PERIOD_LINE_RE.match(line):
                start = i + 1
                break
        bodies.append(lines[start:])

    repeated = _repeated_header_lines(bodies)
    cleaned: list[str] = []
    for lines in bodies:
        for line in lines:
            if not line or _PAGE_FOOTER_RE.match(line):
                continue
            if line in _PAGE_TITLES or line in repeated:
                continue
            cleaned.append(line)
    return cleaned, repeated


def _account_title_block(pages_text: list[str], header_lines: set[str]) -> str:
    """The text that names this account: page 1's "FIDELITY ACCOUNT
    <registration>" title down to its account number, plus the
    registration line every later page repeats.

    Deliberately narrow. The wrapper type has to be read from somewhere,
    and the obvious "search the document for ROTH/IRA/401(k)" is wrong:
    every Fidelity statement closes with legal text naming most of them
    generically, so a whole-document search reports a retirement account
    for a plain taxable one.
    """
    lines = [line.strip() for line in pages_text[0].splitlines()] if pages_text else []
    block: list[str] = []
    for i, line in enumerate(lines):
        if _ACCOUNT_TITLE_RE.match(line):
            for following in lines[i:]:
                block.append(following)
                if _ACCOUNT_ANY_RE.search(following):
                    break
            break
    block.extend(sorted(header_lines))
    return "\n".join(block)


def _account_type(title_block: str) -> str:
    """Maps the account title block onto a normalized account type.

    Only tax-advantaged wrappers get their own name; every ordinary
    taxable registration reports _ACCOUNT_TYPE - see its comment for
    why."""
    return common.classify_account_type(title_block, _ACCOUNT_TYPE)


def _account_identity(combined: str, header_lines: set[str]) -> str:
    """Returns the fullest account identifier the statement still
    carries - everything left after the redaction mask is removed.

    Not truncated to the last four digits here because a longer
    identifier scores better below when several redacted occurrences are
    compared - but the full value returned is used by the caller only to
    derive that last-4 label (see parse(), which never stores the full
    identifier in provider_account_id or anywhere else in the output).

    Redaction masks vary in how much they leave (this account's January
    statement keeps nothing, February onward keeps four digits, and the
    number is printed twice per statement with different amounts
    masked), so every occurrence is scored and the richest wins.
    """
    candidates = [m.group(1) for m in _ACCOUNT_ANY_RE.finditer(combined)]
    # Only the last token of a header line - the number itself. Taking
    # the whole line would fold its label in too, which costs nothing
    # while the label is masked away but turns "Account # 123456789" on
    # an unredacted statement into the identifier "Account123456789".
    candidates += [
        line.split()[-1] for line in sorted(header_lines)
        if line.split() and any(c.isdigit() for c in line.split()[-1])
    ]
    best = ""
    for candidate in candidates:
        # Keep only what is really printed: drop the mask runs, then any
        # separator, leaving the digits/letters the statement disclosed.
        identifier = re.sub(r"[^A-Za-z0-9]", "", _MASK_CHARS_RE.sub("", candidate))
        if len(identifier) > len(best):
            best = identifier
    return best


def _split_trade_tail(rest: str) -> tuple[float | None, float | None]:
    """Splits a Securities Bought & Sold row's trailing "Total Cost
    Basis / Transaction Cost / Transaction Amount" columns, read from
    the right because Fidelity prints no placeholder at all in the cost
    basis column for a buy (leaving two tokens, "- -$1,004,100.00")
    while a sell fills all three ("70,722.79f - 73,813.45"). Returns
    (transaction_cost, amount); the cost basis itself has no home in the
    brokerage_transactions schema and is dropped.
    """
    tokens = rest.split()
    amount = _amount(tokens[-1])
    if amount is None:
        raise ValueError(f"securities bought & sold row has no transaction amount: {rest!r}")
    transaction_cost = _amount(tokens[-2]) if len(tokens) >= 2 else None
    return transaction_cost, amount


def _check_holdings(holdings: list[dict], text: str) -> None:
    m = _TOTAL_HOLDINGS_RE.search(text)
    if not m:
        return
    want = _amount(m.group(1))
    got = round(sum(h["current_value"] for h in holdings if h["current_value"] is not None), 2)
    if want is not None and abs(got - want) > _EPS:
        raise ValueError(
            f"parsed holdings total {got} does not match statement's "
            f"'Total Holdings' {want} (off by {round(got - want, 2)})"
        )


def _check_trades(trades: list[dict], text: str) -> None:
    for regex, action, label in (
        (_TOTAL_BOUGHT_RE, "Buy", "Total Securities Bought"),
        (_TOTAL_SOLD_RE, "Sell", "Total Securities Sold"),
    ):
        m = regex.search(text)
        if not m:
            continue
        want = _amount(m.group(1))
        got = round(sum(t["amount"] for t in trades if t["action"] == action), 2)
        if want is not None and abs(got - want) > _EPS:
            raise ValueError(
                f"parsed securities {action.lower()} total {got} does not match "
                f"statement's {label!r} {want} (off by {round(got - want, 2)})"
            )


def _check_income(income: list[dict], text: str) -> None:
    m = _TOTAL_INCOME_RE.search(text)
    if not m:
        return
    want = _amount(m.group(1))
    got = round(sum(t["amount"] for t in income), 2)
    if want is not None and abs(got - want) > _EPS:
        raise ValueError(
            f"parsed dividends/interest total {got} does not match statement's "
            f"'Total Dividends, Interest & Other Income' {want} (off by {round(got - want, 2)})"
        )


# Deliberately NOT reconciled against the statement's own "Realized
# Gains and Losses from Sales" summary, though the temptation is
# obvious. That summary counts a different population: July 2026's
# printed "Short-term Gain 24,104.00" is the 20,716.79 of gains noted
# under settled trades PLUS 3,387.21 from trades still pending
# settlement, and its loss figure carries 1,710.99 that appears in no
# per-row note at all. Per-row realized figures are correct for the rows
# that print them; totalling them and comparing to that summary compares
# two different things, and a check that fires on a correct statement is
# worse than no check.


def _check_transfers(transfers: list[dict], text: str) -> None:
    for regex, action, label in (
        (_TOTAL_DEPOSITS_RE, _DEPOSIT_ACTION, "Total Deposits"),
        (_TOTAL_WITHDRAWALS_RE, _WITHDRAWAL_ACTION, "Total Withdrawals"),
        (_TOTAL_EXCHANGES_IN_RE, _TRANSFER_IN_ACTION, "Total Exchanges In"),
        (_TOTAL_EXCHANGES_OUT_RE, _TRANSFER_OUT_ACTION, "Total Exchanges Out"),
    ):
        m = regex.search(text)
        if not m:
            continue
        want = _amount(m.group(1))
        got = round(sum(t["amount"] for t in transfers if t["action"] == action), 2)
        if want is not None and abs(got - want) > _EPS:
            raise ValueError(
                f"parsed {action.lower()} total {got} does not match statement's "
                f"{label!r} {want} (off by {round(got - want, 2)})"
            )


def _check_core_fund_activity(core: list[dict], text: str) -> None:
    """Verifies the core sweep ledger two independent ways: against its
    own printed running balance, and against the section's printed
    total. Between them these catch a dropped, duplicated or
    wrongly-signed row - which is also why nothing else (notably the
    Deposits section, whose money is already here) may be mixed into
    these rows.
    """
    for previous, current in zip(core, core[1:]):
        if previous.get("_balance") is None or current.get("_balance") is None:
            continue
        delta = round(current["_balance"] - previous["_balance"], 2)
        if abs(delta - current["amount"]) > _EPS:
            raise ValueError(
                f"core fund activity on {current['date']} moves the balance by {delta} "
                f"but its amount is {current['amount']}"
            )
    m = _TOTAL_CORE_RE.search(text)
    if not m:
        return
    want = _amount(m.group(1))
    got = round(sum(t["amount"] for t in core), 2)
    if want is not None and abs(got - want) > _EPS:
        raise ValueError(
            f"parsed core fund activity total {got} does not match statement's "
            f"'Total Core Fund Activity' {want} (off by {round(got - want, 2)})"
        )


def _new_holding(m: re.Match, holding_type: str, subtype: str, margin_words: set[str],
                 price_as_of: str, year_end: bool = False) -> dict:
    """Builds one brokerage_holdings row from a matched Holdings line.
    The description is kept under a private "_description" key while
    continuation lines are still being appended to it; _finish_holdings
    below turns that into the row's real description/symbol.
    """
    description = m.group("description")
    position_type = ""
    if description.split()[0] in margin_words:
        description = description[1:]
        position_type = "Margin"

    row = {
        "symbol": "",
        "_description": description,
        "quantity": _amount(m.group("quantity")),
        "price": _amount(m.group("price")),
        "current_value": _amount(m.group("value")),
        "cost_basis_total": _amount(m.group("cost")),
        "type": holding_type,
        "currency_code": _CURRENCY,
    }
    # The last column pairs an estimated annual income with an estimated
    # yield, the yield trailing the security's name on a wrapped line
    # rather than sitting in the column - see _extend_holding, which is
    # where it is picked up.
    # A year-end report's last cell is "Income Earned" (trailing-period
    # income), not the EAI/EY estimate every other statement prints there.
    eai = _amount(m.group("eai")) if m.group("eai") and not year_end else None
    if eai is not None:
        row["estimated_annual_income"] = eai
    if subtype:
        row["subtype"] = subtype
    if position_type:
        row["position_type"] = position_type
    if price_as_of:
        row["price_as_of"] = price_as_of
    if holding_type == "Core Account":
        row["is_cash_equivalent"] = True
    return row


def _extend_holding(row: dict, line: str) -> None:
    """Appends a Holdings continuation line's name fragment to the row's
    pending description, dropping the estimated-yield figure that trails
    it, the money-market "-- 7-day yield" note, and the bare figures a
    sub-total prints on the line *above* its own "Total ..." label.
    """
    if line.startswith("--") or "7-day yield" in line:
        return
    if row.get("type") == "Core Account" and line.startswith("For balances below "):
        return
    if _is_bare_figures(line):
        return
    yield_match = _TRAILING_YIELD_RE.search(line)
    if yield_match and "estimated_yield" not in row:
        estimated_yield = _amount(yield_match.group(0))
        if estimated_yield is not None:
            row["estimated_yield"] = estimated_yield
    text = _TRAILING_YIELD_RE.sub("", line).strip()
    if yield_match:
        # The EAI ($) / EY (%) cell wraps onto its own line, and that
        # line always leads with a placeholder dash for a blank EAI($)
        # even when the yield itself printed fine - "- 3.350", "- 5.890%"
        # - so once the yield match above has been stripped off the end,
        # a lone trailing "-" left behind is that placeholder, not real
        # name text. Left in, it silently glued itself onto the end of
        # every holding whose EAI($) column happened to be blank -
        # "WELLTOWER INC COM -" - which also broke CUSIP name-matching
        # for that holding, since nothing else in the project ever prints
        # the name with the stray dash attached.
        text = re.sub(r"(?:^|\s)-$", "", text).strip()
    if text:
        row["_description"] += " " + text


def _finish_holdings(holdings: list[dict]) -> None:
    for row in holdings:
        description = row.pop("_description")
        option = _OPTION_SYMBOL_RE.search(description)
        if option:
            # Leave the underlying's "(AVGO)" in the description - it is
            # real information, and the contract code is what identifies
            # the position.
            row["symbol"] = option.group(1)
            description = description.replace(option.group(0), "")
        else:
            tickers = _TICKER_RE.findall(description)
            if tickers:
                row["symbol"] = tickers[-1]
                description = _TICKER_RE.sub("", description)
            elif row.get("type") == "Core Account":
                symbol = description.split()[0] if description.split() else ""
                if symbol in {"CASH", "FCASH"}:
                    row["symbol"] = symbol
        row["description"] = " ".join(description.split())


def _collect_realized(row: dict, line: str) -> None:
    """Accumulates a sale's realized gain/loss from the notes under it.

    Signed the way the statement's own summary adds up: a gain is
    positive, a loss negative, and a wash-sale disallowed loss positive
    because it is added back. Summed across every row this reproduces
    the "Net Gain/Loss" the statement prints - see _check_realized.
    """
    # A figure on its own line, directly under a disallowed-loss label,
    # belongs to it. The identical figure that follows "Wash sale of:"
    # is the same money reported a second way and must not be added
    # again, so only an armed label consumes one.
    if row.pop("_await_disallowed", False) and _BARE_NUM_RE.match(line.strip()):
        amount = _amount(line.strip())
        if amount is not None:
            row["realized_gain"] = round(row.get("realized_gain", 0.0) + amount, 2)
            return
    if _DISALLOWED_LABEL_RE.search(line):
        row["_await_disallowed"] = True
        return

    for m in _REALIZED_RE.finditer(line):
        amount = _amount(m.group("amount"))
        if amount is None:
            continue
        if m.group("kind").lower() == "loss" and not m.group("disallowed"):
            amount = -amount
        row["realized_gain"] = round(row.get("realized_gain", 0.0) + amount, 2)
        term = m.group("term").capitalize().replace("-t", "-t")
        seen = row.get("realized_gain_term", "")
        # A row reporting both terms has no single one to report.
        row["realized_gain_term"] = term if seen in ("", term) else ""


def _option_contract_symbol(name: str) -> str:
    """Builds the OCC contract code for an option described in words,
    or "" if the text does not describe one.

    The output has to be byte-identical to the code Holdings prints for
    the same contract, because that is the only thing making the two
    sections' rows the same security: "CALL (AVGO) ... JUL 31 26 $400"
    here has to produce the "AVGO260731C400" that _finish_holdings reads
    straight out of the Holdings line. The strike is the one part where
    that could drift, so it is normalized rather than copied - a
    thousands separator comes out, and a trailing zero after the decimal
    point comes off, so $82.50 and $82.5 cannot become two securities.
    """
    m = _OPTION_DESC_RE.search(name)
    if not m:
        return ""
    month = _OPTION_MONTHS.get(m.group("month").upper())
    if month is None:
        return ""
    strike = m.group("strike").replace(",", "")
    if "." in strike:
        strike = strike.rstrip("0").rstrip(".")
    return "{}{}{:02d}{:02d}{}{}".format(
        m.group("underlying").upper(),
        m.group("year"),
        month,
        int(m.group("day")),
        "C" if m.group("kind").upper() == "CALL" else "P",
        strike,
    )


def _strip_notes(text: str) -> str:
    """Removes the trade-date and realized gain/loss annotations Fidelity
    prints among an activity row's security name, and collapses the
    whitespace that leaves behind."""
    return " ".join(_ACTIVITY_NOTE_RE.sub(" ", text).split())


def _corporate_amount(values: str | None) -> float:
    """The cash an "Other Activity In/Out" row moved. The row's trailing
    columns are Price, Transaction Cost, Amount; the last one is the
    figure that matters. "-" in every column (the common share-only
    exchange) reads as 0.00."""
    if not values:
        return 0.0
    tokens = values.split()
    amount = _amount(tokens[-1]) if tokens else None
    return amount if amount is not None else 0.0


# Fidelity wraps a payout annotation in *asterisks* and prints the
# per-share figure inside it ("*EXCHANGED FOR CUSIP ... + $11.45* MER
# PAYOUT"). Once _CORPORATE_NOTE_RE has cut the annotation at its marker,
# these are what can be left dangling on the security's name.
_CORPORATE_NOTE_RESIDUE_RE = re.compile(r"[*+]|\$[\d,]+\.\d+")


def _collect_corporate_note(row: dict, line: str) -> None:
    """Reads the annotations under an Other Activity In/Out row, and
    appends whatever is left of the line to the security's name.

    A merger's notes carry the one fact these rows exist to record - the
    identifier of the security on the other side of the exchange - in
    one of two directions depending on which leg is being read:

        ZENITH GLOBAL MUNI FD INC 777777GG7 Merger 1,960.000
        COM STK USD0.1 MER FROM 666666FF6        <- came from this one
        #REOR M0099887766001

        ZENITH FUSION HOLDINGS COM 666666FF6 Merger -1,600.000
        EXCHANGED FOR 1.225 SHARES OF
        CUSIP 777777GG7 MER PAYOUT #REOR         <- went to this one

    Note that the first line of each also carries part of the name
    ("COM STK USD0.1"), which is why the annotation is cut at its marker
    rather than the whole line discarded - the name has to come out
    matching the holdings section's, or the row cannot be tied to a
    ticker at all (see _finish_activity).
    """
    if row.pop("_await_reference", False):
        m = _REOR_CONTINUATION_RE.match(line.strip())
        if m:
            row.setdefault("reference", m.group("reference"))
            line = line.strip()[m.end():]
    if not row.get("related_security_id"):
        for pattern in (_MERGER_FROM_RE, _MERGER_TO_RE):
            m = pattern.search(line)
            if m:
                row["related_security_id"] = m.group("cusip")
                break
    ref = _REOR_REF_RE.search(line)
    if ref and not row.get("reference"):
        row["reference"] = ref.group("reference")
    elif _REOR_WRAPPED_RE.search(line):
        row["_await_reference"] = True
    text = _CORPORATE_NOTE_RE.sub("", line)
    text = " ".join(_CORPORATE_NOTE_RESIDUE_RE.sub(" ", text).split())
    if text:
        row["_name"] += " " + text


def _clean_internal_transfer_name(text: str) -> tuple[str, str]:
    refs = _INTERNAL_TRANSFER_REF_RE.findall(text)
    name = _INTERNAL_TRANSFER_REF_RE.sub("", text)
    name = _INTERNAL_TRANSFER_VALUE_LABEL_RE.sub("", name)
    return " ".join(name.split()), refs[0] if refs else ""


def _collect_internal_transfer_value(row: dict, line: str) -> None:
    m = _INTERNAL_TRANSFER_VALUE_RE.search(line)
    if m:
        value = _amount(m.group("amount"))
        if value is not None:
            row["amount"] = -value if row["action"] == _TRANSFER_OUT_ACTION else value
        name, ref = _clean_internal_transfer_name(line[:m.start()])
        if ref and not row.get("reference"):
            row["reference"] = ref
        if name:
            row["_name"] += " " + name
        return

    if _INTERNAL_TRANSFER_VALUE_LABEL_RE.search(line):
        name, ref = _clean_internal_transfer_name(line)
        if ref and not row.get("reference"):
            row["reference"] = ref
        if name:
            row["_name"] += " " + name
        row["_await_value"] = True
        return

    if row.pop("_await_value", None):
        stripped = line.strip()
        m = _BARE_MONEY_RE.match(stripped) or _TRANSFER_VALUE_CONTINUATION_RE.match(stripped)
        if m:
            value = _amount(m.group("amount"))
            if value is not None:
                row["amount"] = -value if row["action"] == _TRANSFER_OUT_ACTION else value
            return

    name, ref = _clean_internal_transfer_name(line)
    if ref and not row.get("reference"):
        row["reference"] = ref
    if name:
        row["_name"] += " " + name


def _finish_activity(rows: list[dict], holdings: list[dict]) -> None:
    """Turns each activity row's pending security name into its
    description, and fills in the ticker, which the Activity sections
    never print - they identify a security by CUSIP (kept as
    security_id) and name only. Holdings do print it, so a row whose
    security is also held gets its symbol from there; one that isn't
    (a position closed out during the period) keeps just the CUSIP.
    """
    symbols: dict[str, str] = {}
    for holding in holdings:
        key = _norm_key(holding["description"])
        if key and holding["symbol"]:
            symbols.setdefault(key, holding["symbol"])

    for row in rows:
        row.pop("_await_disallowed", None)
        row.pop("_await_reference", None)
        row.pop("_await_value", None)
        row.pop("_balance", None)
        # Stripped again here, not just per line: an annotation is
        # regularly split across the lines it wraps over, so it only
        # becomes matchable once the name has been joined back up.
        name = _strip_notes(row.pop("_name"))
        # A transfer row has no action label of its own to prefix - its
        # description is already the whole story ("Wire Trans From
        # Bank") - so join only the parts that exist.
        row["description"] = " ".join(part for part in (row.pop("_label"), name) if part)
        # An option is identified by its own contract code, never by the
        # underlying's ticker, which is printed right there in the same
        # line ("CALL (AVGO) ...") and would merge every contract into
        # the stock it is written on. Built from the description rather
        # than looked up, so a contract closed inside the period - the
        # kind that realizes the gain - resolves like any other.
        symbol = _option_contract_symbol(name)
        if not symbol:
            symbol = symbols.get(_norm_key(name))
        if symbol:
            row["symbol"] = symbol


def parse(pages_text: list[str], pdf_path: str) -> dict:
    combined = "\n".join(pages_text)
    start, end = _statement_period(combined)
    statement_date = end.isoformat() if end else ""

    lines, header_lines = _clean_pages(pages_text)
    year_end = any(_YEAR_END_HOLDINGS_HEADER_RE.match(line) for line in lines)
    holding_row_re = _HOLDING_ROW_RE_YEAR_END if year_end else _HOLDING_ROW_RE
    account = _account_identity(combined, header_lines)
    account_type = _account_type(_account_title_block(pages_text, header_lines))
    margin_words = _margin_first_words(pdf_path)

    holdings: list[dict] = []
    trades: list[dict] = []
    income: list[dict] = []
    core: list[dict] = []
    transfers: list[dict] = []
    exchanges: list[dict] = []
    internal_transfers: list[dict] = []
    corporate: list[dict] = []

    section: str | None = None
    section_label = ""
    holding_type = ""
    holding_subtype = ""
    pending: dict | None = None

    for line in lines:
        base = _base_label(line)
        # The same line with any leading column figures removed, so a
        # total is recognized whether or not its own figures precede its
        # label. A real holding row starts with its description, so
        # nothing is stripped from one.
        labelled = _LEADING_VALUES_RE.sub("", line)

        # A "Total ..." line always ends the run of rows above it, so no
        # later line can be mistaken for a continuation of the last one;
        # a section's own grand total also ends the section. Holdings'
        # per-sub-category totals ("Total Equity ETPs") deliberately
        # don't - only "Total Holdings" closes that section.
        if labelled.startswith("Total ") or labelled.startswith("Net Securities Bought"):
            pending = None
            if _base_label(labelled).startswith((
                "Total Holdings",
                "Net Securities Bought",
                "Total Dividends, Interest & Other Income",
                "Total Core Fund Activity",
                "Total Deposits",
                "Total Withdrawals",
                "Total Exchanges In",
                "Total Exchanges Out",
                "Total Transfers Between Fidelity Accounts",
            )):
                section = None
            continue

        if base in _HOLDING_SECTIONS:
            section, holding_type, holding_subtype, pending = "holdings", base, "", None
            continue
        if base in _ACTIVITY_SECTIONS:
            section, section_label, pending = _ACTIVITY_SECTIONS[base], base, None
            continue
        if section == "holdings" and base in _HOLDING_SUBSECTIONS:
            holding_subtype, pending = base, None
            continue

        if section is None or section == "skip":
            continue

        if section == "holdings":
            if _is_bare_figures(line):
                continue
            match = holding_row_re.match(line)
            if match:
                pending = _new_holding(
                    match, holding_type, holding_subtype, margin_words, statement_date,
                    year_end,
                )
                holdings.append(pending)
            elif pending is not None:
                _extend_holding(pending, line)
            continue

        if section == "trades":
            match = _TRADE_ROW_RE.match(line)
            if match:
                transaction_cost, amount = _split_trade_tail(match.group("rest"))
                pending = {
                    "date": _resolve_date(match.group("date"), start, end),
                    "_label": match.group("label"),
                    "_name": match.group("name"),
                    "amount": amount,
                    "action": _ACTIONS[match.group("label")],
                    "security_id": match.group("security_id"),
                    "security_id_type": "CUSIP",
                    "quantity": _amount(match.group("quantity")),
                    "price": _amount(match.group("price")),
                    "currency_code": _CURRENCY,
                }
                if transaction_cost is not None:
                    # Printed as a negative (a debit), but stored as a
                    # magnitude to match what ingestBrokerageTransactionsCSV
                    # writes for Fidelity's own CSV export - the two land
                    # in the same column. `amount` is already net of it.
                    pending["commission_and_fees"] = abs(transaction_cost)
                trades.append(pending)
            elif pending is not None:
                # Anything else under a trade continues the security's
                # name; the realized gain/loss, wash-sale and confirm
                # notes a sell prints do not - whether they take a line
                # to themselves or trail the name on one. Read the
                # realized figures out of them before they are stripped.
                _collect_realized(pending, line)
                text = _strip_notes(line)
                if text and not _TRADE_NOTE_RE.match(text) and not _BARE_NUM_RE.match(text):
                    pending["_name"] += " " + text
            continue

        if section == "income":
            match = _INCOME_ROW_RE.match(line)
            if match:
                pending = {
                    "date": _resolve_date(match.group("date"), start, end),
                    "_label": match.group("label"),
                    "_name": match.group("name"),
                    "amount": _amount(match.group("amount")),
                    "action": _ACTIONS[match.group("label")],
                    "security_id": match.group("security_id"),
                    "security_id_type": "CUSIP",
                    "quantity": _amount(match.group("quantity")),
                    "price": _amount(match.group("price")),
                    "currency_code": _CURRENCY,
                }
                income.append(pending)
            elif pending is not None:
                text = _strip_notes(line)
                if text and not _BARE_TRADE_DATE_RE.match(text):
                    pending["_name"] += " " + text
            continue

        if section == "transfers":
            match = _TRANSFER_ROW_RE.match(line)
            if match:
                action = _DEPOSIT_ACTION if section_label == "Deposits" else _WITHDRAWAL_ACTION
                pending = {
                    "date": _resolve_date(match.group("date"), start, end),
                    "_label": "",
                    "_name": match.group("description"),
                    "amount": _amount(match.group("amount")),
                    "action": action,
                    "currency_code": _CURRENCY,
                }
                transfers.append(pending)
            elif pending is not None:
                # The paying institution's own details wrap onto the next
                # line ("WELLS FARGO BANK NA ******4781").
                pending["_name"] += " " + line
            continue

        if section == "exchanges":
            match = _EXCHANGE_ROW_RE.match(line)
            if match:
                action = (
                    _TRANSFER_IN_ACTION
                    if section_label == "Exchanges In" or match.group("label") == "Transferred From"
                    else _TRANSFER_OUT_ACTION
                )
                reference = " ".join(match.group("reference").split())
                pending = {
                    "date": _resolve_date(match.group("date"), start, end),
                    "_label": "",
                    "_name": f"{match.group('label')} {reference}".strip(),
                    "amount": _amount(match.group("amount")),
                    "action": action,
                    "transaction_type": _INTERNAL_TRANSFER_TYPE,
                    "subtype": "In" if action == _TRANSFER_IN_ACTION else "Out",
                    "symbol": "CASH",
                    "reference": reference,
                    "currency_code": _CURRENCY,
                }
                exchanges.append(pending)
            elif pending is not None:
                pending["_name"] += " " + line
            continue

        if section == "internal_transfers":
            match = _INTERNAL_TRANSFER_ROW_RE.match(line)
            if match:
                name, ref = _clean_internal_transfer_name(match.group("name"))
                action = (
                    _TRANSFER_OUT_ACTION
                    if match.group("label") == "Transferred To"
                    else _TRANSFER_IN_ACTION
                )
                pending = {
                    "date": _resolve_date(match.group("date"), start, end),
                    "_label": action,
                    "_name": name,
                    "amount": 0.0,
                    "action": action,
                    "transaction_type": _INTERNAL_TRANSFER_TYPE,
                    "subtype": "Out" if action == _TRANSFER_OUT_ACTION else "In",
                    "security_id": match.group("security_id"),
                    "security_id_type": "CUSIP",
                    "quantity": _amount(match.group("quantity")),
                    "price": _amount(match.group("price")),
                    "currency_code": _CURRENCY,
                }
                ref = match.group("reference") or ref
                if ref:
                    pending["reference"] = " ".join(ref.split())
                internal_transfers.append(pending)
            elif pending is not None:
                _collect_internal_transfer_value(pending, line)
            continue

        if section == "corporate":
            match = _CORPORATE_ROW_RE.match(line)
            if match:
                direction = _CORPORATE_DIRECTIONS.get(section_label, "")
                label = match.group("label")
                pending = {
                    "date": _resolve_date(match.group("date"), start, end),
                    # No label prefix on the description, unlike every
                    # other activity row: `action` already says what
                    # happened ("Merger Out"), and leaving the
                    # description as the security's name alone is what
                    # lets a row for a position the statement no longer
                    # holds still be matched to it by name - which is
                    # the only way the outgoing leg of a merger can be
                    # tied to the security it replaced (see
                    # foldSecurityEvidence).
                    "_label": "",
                    "_name": match.group("name"),
                    # Usually a share exchange moves no cash and the
                    # Transaction Amount column is "-", which _corporate_amount
                    # reads as 0.00 - not a figure invented to satisfy a
                    # required key. But a merger can also pay cash in lieu
                    # (a CVR payout, a fractional-share buyout): when the
                    # Amount column prints a real figure, that is the cash
                    # the account actually received and it is kept. The
                    # money-market sweep that mirrors it in Core Fund
                    # Activity is not persisted (see the module docstring),
                    # so this does not double-count. transaction_type is
                    # what keeps these rows out of a trade or cash-flow query.
                    "amount": _corporate_amount(match.group("values")),
                    "action": (label + " " + direction).strip(),
                    "transaction_type": _CORPORATE_TYPE,
                    "subtype": direction,
                    "security_id": match.group("security_id"),
                    "security_id_type": "CUSIP",
                    "quantity": _amount(match.group("quantity")),
                    "currency_code": _CURRENCY,
                }
                corporate.append(pending)
            elif pending is not None:
                _collect_corporate_note(pending, line)
            continue

        if section == "core":
            match = _CORE_ROW_RE.match(line)
            if not match:
                # Execution annotations ("MORNING TRADE @ 1",
                # "REINVEST @ $1.000") - nothing to record.
                continue
            # "-" in a running-balance column means zero, not unknown -
            # a ledger row always leaves a balance behind. Recording it
            # as 0.0 rather than None also keeps it inside
            # _check_core_fund_activity's continuity check, so if the
            # placeholder ever meant something else the check would say
            # so instead of quietly skipping the row.
            balance = match.group("balance")
            description = match.group("description")
            core.append({
                "date": _resolve_date(match.group("date"), start, end),
                "_label": "",
                "_name": description,
                "action": "Core Fund Activity",
                "transaction_type": _CORE_FUND_ACTIVITY_TYPE,
                "symbol": match.group("account_type"),
                "quantity": _amount(match.group("quantity")),
                "price": _amount(match.group("price")),
                "amount": _amount(match.group("amount")),
                "_balance": 0.0 if balance in ("-", "--") else _amount(balance),
                "currency_code": _CURRENCY,
            })

    _finish_holdings(holdings)
    _check_core_fund_activity(core, "\n".join(lines))
    _finish_activity(trades + income + transfers + exchanges + internal_transfers + corporate, holdings)

    cleaned_text = "\n".join(lines)
    _check_holdings(holdings, cleaned_text)
    _check_trades(trades, cleaned_text)
    _check_income(income, cleaned_text)
    _check_transfers(transfers + exchanges, cleaned_text)

    for rows in (holdings, trades, income, transfers, exchanges, internal_transfers, corporate):
        # "account" is a display label, truncated to the last 4 digits
        # like every other parser (see statement.go's Transaction doc).
        # The fullest identifier _account_identity() found is used only
        # to compute this and is otherwise discarded - never written to
        # provider_account_id or any other output field.
        common.tag_account(rows, common.last4_digits(account), account_type)

    return {
        "institution": _INSTITUTION,
        "statementDate": statement_date,
        "tables": {
            "brokerage_holdings": holdings,
            "brokerage_transactions": trades + income + transfers + exchanges + internal_transfers + corporate,
        },
    }
