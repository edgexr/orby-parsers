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
