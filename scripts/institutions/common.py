"""Helpers shared across institution-specific statement parsers."""

import re


def parse_amount(raw: str) -> float:
    """Parse a statement amount like '-1,234.56' or '$1,234.56' into a float."""
    return float(raw.replace(",", "").replace("$", ""))


def last4_digits(s: str) -> str:
    """Strip non-digit characters and return the trailing 4 digits, e.g.
    '0000 0000 0000 7777' -> '7777'. Returns whatever digits are found
    (possibly fewer than 4, possibly '') if the input is short.
    """
    digits = re.sub(r"\D", "", s)
    return digits[-4:]


# Ordered patterns mapping an account title, product name or registration
# line onto the label this codebase uses for that kind of account.
#
# ORDER IS SIGNIFICANT, and the rule is "most specific first": "Roth
# 401(k)" must be tested before "401(k)", and "Rollover IRA"/"Roth IRA"
# before the bare "IRA", or the general pattern swallows the specific one
# and a Roth is reported as an ordinary IRA. Adding a pattern means
# deciding where in this list it belongs, not appending to the end.
#
# This table lives here, once, because it used to live twice - a copy in
# fidelity_brokerage.py and another in vanguard_brokerage.py - and the two
# had already drifted: Fidelity recognized HSA and 529, Vanguard did not,
# so the same account type read differently depending on which custodian
# sent the statement.
#
# The labels are the document's own vocabulary, deliberately not a
# normalized enum. Normalization happens once on the Go side, at the
# ingest boundary (pkg/ingest/account_type.go), so a parser - including a
# community parser written against the published contract - never has to
# know the enum and can never get it wrong. Keep the two tables in step
# when adding a pattern.
ACCOUNT_TYPE_PATTERNS = [
    # Roth variants, before anything matching their base form.
    (re.compile(r"\bROTH\s*\(?\s*401\s*\(?\s*K\s*\)?", re.I), "Roth 401(k)"),
    (re.compile(r"\b401\s*\(?\s*K\s*\)?\s*ROTH\b", re.I), "Roth 401(k)"),
    (re.compile(r"\bROTH\s+IRA\b", re.I), "Roth IRA"),
    (re.compile(r"\bROTH\b", re.I), "Roth IRA"),
    # Qualified IRAs, before the bare IRA.
    (re.compile(r"\bROLLOVER\s+IRA\b|\bROLLOVER\b", re.I), "Rollover IRA"),
    (re.compile(r"\bTRADITIONAL\s+IRA\b|\bTRADITIONAL\b", re.I), "Traditional IRA"),
    (re.compile(r"\bINHERITED\s+IRA\b|\bINHERITED\b|\bBENEFICIARY\s+IRA\b", re.I), "Inherited IRA"),
    (re.compile(r"\bSEP[\s-]*IRA\b|\bSEP\b", re.I), "SEP IRA"),
    (re.compile(r"\bSIMPLE[\s-]*IRA\b", re.I), "SIMPLE IRA"),
    (re.compile(r"\b401\s*\(?\s*K\s*\)?", re.I), "401(k)"),
    (re.compile(r"\b403\s*\(?\s*B\s*\)?", re.I), "403(b)"),
    (re.compile(r"\b457\s*\(?\s*B?\s*\)?", re.I), "457(b)"),
    (re.compile(r"\bHSA\b|\bHEALTH\s+SAVINGS\b", re.I), "HSA"),
    (re.compile(r"\b529\b|\bCOLLEGE\s+SAVINGS\b", re.I), "529"),
    # The bare IRA, only after every qualified form has had its turn.
    (re.compile(r"\bIRA\b", re.I), "IRA"),
    # Liabilities before the cash patterns - a credit card line often
    # also says "Account".
    (re.compile(r"\bCREDIT\s*CARD\b|\bVISA\b|\bMASTERCARD\b|\bAMERICAN\s+EXPRESS\b", re.I), "Credit Card"),
    (re.compile(r"\bMORTGAGE\b|\bHELOC\b|\bHOME\s+EQUITY\b", re.I), "Mortgage"),
    (re.compile(r"\bLOAN\b|\bLINE\s+OF\s+CREDIT\b", re.I), "Loan"),
    # Cash. Money market before savings: "money market savings" is a
    # money market account.
    (re.compile(r"\bMONEY\s*MARKET\b|\bMMA\b|\bMMKT\b", re.I), "Money Market"),
    (re.compile(r"\bCERTIFICATE\s+OF\s+DEPOSIT\b", re.I), "CD"),
    (re.compile(r"\bCHECKING\b|\bCHKG\b", re.I), "Checking"),
    (re.compile(r"\bSAVINGS\b|\bSVGS\b", re.I), "Savings"),
    # Trust before the generic brokerage forms: a trust brokerage account
    # says both.
    (re.compile(r"\bTRUST\b|\bREVOCABLE\b|\bIRREVOCABLE\b", re.I), "Trust"),
]


def classify_account_type(text: str, default: str = "") -> str:
    """Returns the account-type label for an account title, product name
    or registration line, or default if nothing matches.

    A parser that has a sensible fallback for its own documents passes it
    as default - a Fidelity or Vanguard statement with no tax-advantaged
    wrapper named on it is an ordinary taxable brokerage account, so those
    parsers pass "Brokerage". A bank parser with no such guarantee should
    pass the raw product name (or leave it "") rather than guess: an
    unrecognized value is preserved verbatim as account_type_raw and
    reported as unclassified, which is a visible gap someone can fix,
    where a wrong guess is a wrong number nobody notices.
    """
    if not text:
        return default
    for pattern, account_type in ACCOUNT_TYPE_PATTERNS:
        if pattern.search(text):
            return account_type
    return default


def tag_account(transactions: list[dict], account: str, account_type: str) -> None:
    """Sets each transaction's "account"/"accountType" to account/
    account_type, unless a transaction already has one set (e.g. a
    combined statement's own per-row logic already assigned a secondary
    account's values for that transaction) - required by parser_common's
    IO contract, since account/accountType are per-transaction keys, not
    a top-level result key (a single statement/export can cover more
    than one account). Call this once, after building the full
    transaction list, for the common single-account case; a
    combined-statement parser that already sets these per-row for its
    secondary accounts can still call this at the end to fill in the
    primary account's values for every other transaction.
    """
    for txn in transactions:
        txn.setdefault("account", account)
        txn.setdefault("accountType", account_type)


def negate_amounts_and_balances(transactions: list[dict]) -> None:
    """Flips the sign of every transaction's "amount" and "balance" (if
    set) in place.

    A credit card statement's own printed sign convention is easy to
    parse and validate as-is (charges positive/payments negative -
    matching how the statement's own balance-owed figure moves), but
    this codebase's shared schema instead signs every account type the
    same way checking/savings already are: by how the transaction
    affects the money you actually have, not the card issuer's balance
    (negative = you're poorer, e.g. a purchase or a checking withdrawal;
    positive = you're richer, e.g. a paycheck or a credit card payment
    that reduces what you owe). That's the opposite sign from a credit
    card balance's own natural direction, so a credit card parser should
    parse/validate amounts in their natural, as-printed sign first (see
    apply_running_balance), then call this as a final step before
    returning - rather than trying to carry the flipped sign through
    parsing and reconciliation, which is easy to get subtly wrong.
    """
    for txn in transactions:
        txn["amount"] = -txn["amount"]
        if txn["balance"] is not None:
            txn["balance"] = -txn["balance"]


def apply_running_balance(transactions: list[dict], starting_balance: float | None) -> None:
    """Fills in each transaction's "balance" with the running balance
    immediately after it, computed from starting_balance in the order
    transactions is already in (so callers must sort first). No-op if
    starting_balance is None (the statement's own beginning/previous
    balance line wasn't found) or any transaction already has a balance
    (the statement printed a real per-line balance column, which is
    always more trustworthy than one we derive).
    """
    if starting_balance is None:
        return
    if any(t["balance"] is not None for t in transactions):
        return
    running = starting_balance
    for txn in transactions:
        running = round(running + txn["amount"], 2)
        txn["balance"] = running
