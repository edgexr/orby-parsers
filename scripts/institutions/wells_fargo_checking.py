"""Wells Fargo consumer checking/savings statement parser.

Format: monthly statements with a single "Transaction history" table
whose columns are:

    Date | Description | Check No. | Deposits/Additions |
    Withdrawals/Subtractions | Ending Daily Balance

Unlike the Bank of America statements, deposits and withdrawals are NOT
in separately-labeled sections - direction is encoded purely by which
column an amount is printed in, and pdfplumber's extract_text() flattens
those columns away. These two lines are textually the same shape:

    6/1 Zelle From Sharad Mathur On 05/31 Ref # Jpm99Cj56NY0 200.00
    6/1 Zelle to Sharad On 05/31 Ref # Wfct127Zmfsb Meso Dinner 325.00

but the first is a deposit and the second a withdrawal. So signs are
recovered from the statement's own arithmetic instead: the "Ending Daily
Balance" column is printed on the last transaction of each day, which
pins down the day's net change. Each day's amounts are then assigned the
sign combination reproducing that net change exactly (see _solve_day).

Where a day admits more than one such combination - which happens only
when it contains equal amounts, e.g. a payroll deposit and its same-day
reversal - _DIRECTION_HINTS breaks the tie by description. Hints are
only ever a tie-breaker; the balance column always decides.

The finished result is checked against the statement's own printed
"Totals" line and closing balance, and parsing fails loudly rather than
emitting transactions whose signs don't reconcile.
"""

import re
from datetime import datetime

from . import common

# Tolerance for comparing statement amounts, in dollars. Everything is
# printed to the cent, so anything above half a cent is a real mismatch.
_EPS = 0.005

# Upper bound on transactions in one day that _solve_day will brute-force
# (2**n sign combinations). Days this busy don't appear on consumer
# statements; the cap just keeps a malformed parse from hanging.
_MAX_SOLVE_TXNS = 20

# "Wells Fargo Prime Checking", "Wells Fargo Everyday Checking",
# "Wells Fargo Way2Save Savings", etc. Appears on the cover page and
# again above the transaction history.
_ACCOUNT_NAME_RE = re.compile(r"Wells Fargo ([A-Za-z0-9 ]*?(?:Checking|Savings))\b")

# "June 30, 2026" - the statement's closing date, printed at the top of
# every page.
_STATEMENT_DATE_RE = re.compile(r"^([A-Z][a-z]+ \d{1,2}, \d{4})\s*$", re.MULTILINE)

# "Account number: 000000000 (primary account)". Digits may be masked
# with X, so the caller keeps the trailing 4 rather than slicing here.
_ACCOUNT_NUMBER_RE = re.compile(
    r"Account number:\s*([\dXx*]{2,25})", re.IGNORECASE
)

# "Balance on 6/1 18,999.52" - the opening balance, and the seed for the
# whole running-balance reconstruction. The closing "Balance on 6/30
# $12,945.61" matches the same shape, so callers take the first hit.
_BALANCE_ON_RE = re.compile(r"^Balance on \d{1,2}/\d{1,2}\s+\$?([\d,]+\.\d{2})\s*$", re.MULTILINE)

# "Totals $119,153.23 $125,207.14" - deposits then withdrawals, printed
# at the foot of the transaction history and used to validate the parse.
_TOTALS_RE = re.compile(r"^Totals\s+\$([\d,]+\.\d{2})\s+\$([\d,]+\.\d{2})\s*$", re.MULTILINE)

# The transaction history column header, e.g.
#   Date Description Check No. Additions Subtractions Daily Balance
# Reprinted on each page the table continues onto.
_TABLE_HEADER_RE = re.compile(r"^Date\s+Description\s+Check No\.", re.IGNORECASE | re.MULTILINE)

# A transaction row: "M/D <description> <amount> [ending daily balance]".
# The description is lazy so the trailing 1-2 amounts bind to the end of
# the line; capping the run at 2 keeps a decimal inside the description
# from being mistaken for an amount (a third trailing number can only be
# description text, since a row is either a deposit or a withdrawal,
# never both).
_TXN_RE = re.compile(
    r"^(\d{1,2}/\d{1,2})\s+(.*?)((?:\s+\d{1,3}(?:,\d{3})*\.\d{2}){1,2})\s*$"
)

_AMOUNT_RE = re.compile(r"\d{1,3}(?:,\d{3})*\.\d{2}")

# "Ref # Wfct127Zmfsb", "Ref#20260602043000096P1Baaaa00398085216" - the
# issuer's own reference, when printed. Informational only: it takes no
# part in parsing or dedup.
_REFERENCE_RE = re.compile(r"Ref\s*#\s*(\S+)")

# Page furniture and table headers that appear between transaction rows
# where the table spans a page break. Without this they'd be appended to
# the preceding transaction's description as continuation lines.
_NOISE_RE = re.compile(
    r"^(?:"
    r"[A-Z][a-z]+ \d{1,2}, \d{4}"          # page date header
    r"|Page \d+ of \d+"
    r"|=>.*"                                # "=> Wells Fargo ... ( continued)"
    r"|Deposits/.*"                         # wrapped column header, line 1
    r"|Date\s+Description.*"                # wrapped column header, line 2
    r"|The Ending Daily Balance.*"
    r")\s*$",
    re.IGNORECASE,
)

# Description patterns hinting at direction, tried in order, first match
# wins: +1 means the balance goes up, -1 down, and anything unmatched
# gets 0 (no opinion). Order matters - the specific debit forms must be
# tested before the generic credit words they contain. In particular
# "Deposited OR Cashed Check" is a check written against the account (a
# withdrawal) despite starting with "Deposited", and a payroll
# "Reversal" claws back a deposit.
_DIRECTION_HINTS = [
    (re.compile(r"deposited or cashed check", re.IGNORECASE), -1),
    (re.compile(r"\breversal\b", re.IGNORECASE), -1),
    (re.compile(r"\bzelle to\b", re.IGNORECASE), -1),
    (re.compile(r"\btransfer to\b", re.IGNORECASE), -1),
    (re.compile(r"\bbill pay\b", re.IGNORECASE), -1),
    (re.compile(r"\bwithdrawal\b", re.IGNORECASE), -1),
    (re.compile(r"^check \d+", re.IGNORECASE), -1),
    (re.compile(r"money transfer authorized", re.IGNORECASE), -1),
    (re.compile(r"usataxpymt|franchise tax", re.IGNORECASE), -1),
    (re.compile(r"\bpurchase authorized\b", re.IGNORECASE), -1),
    (re.compile(r"\bzelle from\b", re.IGNORECASE), 1),
    (re.compile(r"\binstant pmt from\b", re.IGNORECASE), 1),
    (re.compile(r"\btransfer from\b", re.IGNORECASE), 1),
    (re.compile(r"\bpayroll\b", re.IGNORECASE), 1),
    (re.compile(r"\binterest payment\b", re.IGNORECASE), 1),
    (re.compile(r"\bmoneyline\b", re.IGNORECASE), 1),
    (re.compile(r"\brefund\b", re.IGNORECASE), 1),
    (re.compile(r"\bdeposit\b", re.IGNORECASE), 1),
]


def detect(head_text: str) -> tuple[bool, str]:
    if "wells fargo" not in head_text.lower():
        return False, "'wells fargo' not found"
    if not _ACCOUNT_NAME_RE.search(head_text):
        return False, "'wells fargo' found, but no 'Wells Fargo <name> Checking/Savings' account header"
    if not _TABLE_HEADER_RE.search(head_text):
        return (
            False,
            "'wells fargo' found, but no 'Date Description Check No. ...' transaction history header",
        )
    return True, "matched Wells Fargo checking/savings transaction history"


def _direction_hint(description: str) -> int:
    for pattern, sign in _DIRECTION_HINTS:
        if pattern.search(description):
            return sign
    return 0


def _extract_account_number(combined_text: str) -> str:
    m = _ACCOUNT_NUMBER_RE.search(combined_text)
    if not m:
        return ""
    return common.last4_digits(m.group(1))


def _extract_statement_meta(combined_text: str) -> tuple[str, str]:
    """Returns (account_type, statement_date), e.g. ("Checking",
    "2026-06-30"). Either may be "" if the statement's header isn't found
    or doesn't match the expected format.
    """
    account_type = ""
    m = _ACCOUNT_NAME_RE.search(combined_text)
    if m:
        account_type = "Savings" if "savings" in m.group(1).lower() else "Checking"

    statement_date = ""
    m = _STATEMENT_DATE_RE.search(combined_text)
    if m:
        try:
            statement_date = datetime.strptime(m.group(1), "%B %d, %Y").strftime("%Y-%m-%d")
        except ValueError:
            statement_date = ""

    return account_type, statement_date


def _collect_rows(pages_text: list[str]) -> list[dict]:
    """Pulls the raw transaction history rows out of every page, in
    statement order. Each row is {date "M/D", description, amounts
    [floats], balance float|None}, with continuation lines already folded
    into description and signs not yet decided.
    """
    rows: list[dict] = []
    in_table = False

    for text in pages_text:
        for line in text.split("\n"):
            s = line.strip()
            if not s:
                continue

            if _TABLE_HEADER_RE.match(s):
                in_table = True
                continue
            if not in_table:
                continue
            # The totals line closes the table; what follows (the checks
            # summary, disclosures) must not be read as transactions.
            if s.startswith("Totals"):
                in_table = False
                continue
            if _NOISE_RE.match(s):
                continue

            m = _TXN_RE.match(s)
            if m:
                amounts = [common.parse_amount(a) for a in _AMOUNT_RE.findall(m.group(3))]
                # Two trailing numbers means the row also carries the
                # day's ending daily balance in the last column.
                balance = amounts.pop() if len(amounts) == 2 else None
                rows.append(
                    {
                        "date": m.group(1),
                        "description": m.group(2).strip(),
                        "amount": amounts[0],
                        "balance": balance,
                    }
                )
            elif rows and not re.match(r"^\d{1,2}/\d{1,2}\s", s):
                # A wrapped description line for the row above.
                rows[-1]["description"] += " " + s

    for row in rows:
        row["description"] = re.sub(r"\s+", " ", row["description"]).strip()
    return rows


def _solve_day(amounts: list[float], hints: list[int], delta: float):
    """Finds the +/- assignment for one day's amounts whose signed sum is
    delta (that day's change in ending balance). Returns (signs,
    unambiguous) or (None, False) if no combination reproduces delta.

    Where several combinations work - only possible when the day repeats
    an amount - hints pick the best-scoring one and unambiguous is False.
    """
    n = len(amounts)
    if n > _MAX_SOLVE_TXNS:
        return None, False

    best = None
    best_score = None
    ties = 0
    for mask in range(1 << n):
        total = 0.0
        for i, amount in enumerate(amounts):
            total += amount if (mask >> i) & 1 else -amount
        if abs(total - delta) > _EPS:
            continue
        signs = [1 if (mask >> i) & 1 else -1 for i in range(n)]
        # Reward agreeing with a hint, penalize contradicting one;
        # unhinted rows (hint 0) contribute nothing either way.
        score = sum(h * s for h, s in zip(hints, signs))
        if best_score is None or score > best_score:
            best, best_score, ties = signs, score, 1
        elif score == best_score:
            ties += 1

    if best is None:
        return None, False
    return best, ties == 1


def _assign_signs(rows: list[dict], opening_balance: float | None) -> None:
    """Signs each row's amount in place and fills in its running balance.

    Rows are grouped into runs sharing a date; the last row of each run
    carries that day's printed ending balance, which fixes the day's net
    change and therefore the signs of every amount within it.
    """
    if opening_balance is None:
        raise ValueError("no opening 'Balance on M/D' line found; cannot determine transaction signs")

    running = opening_balance
    i = 0
    while i < len(rows):
        j = i
        while j + 1 < len(rows) and rows[j + 1]["date"] == rows[i]["date"]:
            j += 1
        day = rows[i : j + 1]

        amounts = [r["amount"] for r in day]
        hints = [_direction_hint(r["description"]) for r in day]
        day_end = day[-1]["balance"]

        if day_end is not None:
            signs, _ = _solve_day(amounts, hints, round(day_end - running, 2))
            if signs is None:
                raise ValueError(
                    f"could not reconcile {rows[i]['date']} transactions "
                    f"{amounts} against balance change {running} -> {day_end}"
                )
        else:
            # No printed ending balance for this day (not seen on the
            # sample statement, but don't lose the day if it happens):
            # fall back to description hints, defaulting to a withdrawal.
            signs = [h if h != 0 else -1 for h in hints]

        for row, sign in zip(day, signs):
            row["amount"] = round(sign * row["amount"], 2)
            running = round(running + row["amount"], 2)
            row["balance"] = running

        if day_end is not None and abs(running - day_end) > _EPS:
            raise ValueError(
                f"running balance {running} does not match printed ending "
                f"daily balance {day_end} on {rows[i]['date']}"
            )
        i = j + 1


def _txn_date(date_md: str, statement_date: str) -> str:
    """Converts a "M/D" transaction date to YYYY-MM-DD using the
    statement's closing date for the year. A transaction month after the
    statement month belongs to the previous year (a January statement
    listing December activity).
    """
    month, day = (int(p) for p in date_md.split("/"))
    if not statement_date:
        raise ValueError(f"cannot resolve year for transaction date {date_md}: no statement date")
    stmt_year, stmt_month = int(statement_date[:4]), int(statement_date[5:7])
    year = stmt_year - 1 if month > stmt_month else stmt_year
    return f"{year:04d}-{month:02d}-{day:02d}"


def _validate_totals(combined_text: str, transactions: list[dict]) -> None:
    """Cross-checks the signed amounts against the statement's own
    printed deposit/withdrawal totals, so a mis-signed transaction fails
    the parse instead of being reported as fact.
    """
    m = _TOTALS_RE.search(combined_text)
    if not m:
        return
    want_deposits = common.parse_amount(m.group(1))
    want_withdrawals = common.parse_amount(m.group(2))
    got_deposits = round(sum(t["amount"] for t in transactions if t["amount"] > 0), 2)
    got_withdrawals = round(-sum(t["amount"] for t in transactions if t["amount"] < 0), 2)
    if abs(got_deposits - want_deposits) > _EPS:
        raise ValueError(
            f"parsed deposits {got_deposits} do not match statement total {want_deposits}"
        )
    if abs(got_withdrawals - want_withdrawals) > _EPS:
        raise ValueError(
            f"parsed withdrawals {got_withdrawals} do not match statement total {want_withdrawals}"
        )


def parse(pages_text: list[str], pdf_path: str, vision: dict | None = None) -> dict:  # noqa: ARG001 - pdf_path/vision unused, see bank_statement.py contract
    combined = "\n".join(pages_text)
    account_type, statement_date = _extract_statement_meta(combined)

    balances = _BALANCE_ON_RE.findall(combined)
    opening_balance = common.parse_amount(balances[0]) if balances else None

    rows = _collect_rows(pages_text)
    _assign_signs(rows, opening_balance)

    transactions = [
        {
            "date": _txn_date(row["date"], statement_date),
            "description": row["description"],
            "amount": row["amount"],
            "balance": row["balance"],
            "reference": (
                m.group(1) if (m := _REFERENCE_RE.search(row["description"])) else ""
            ),
        }
        for row in rows
    ]

    _validate_totals(combined, transactions)
    if balances and len(balances) > 1 and transactions:
        closing = common.parse_amount(balances[-1])
        if abs(transactions[-1]["balance"] - closing) > _EPS:
            raise ValueError(
                f"final running balance {transactions[-1]['balance']} does not "
                f"match statement closing balance {closing}"
            )
    common.tag_account(transactions, _extract_account_number(combined), account_type)

    return {
        "institution": "Wells Fargo",
        "statementDate": statement_date,
        "transactions": transactions,
    }
