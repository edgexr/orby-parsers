"""Fidelity Investments - "Portfolio_Positions" holdings/positions CSV
export parser.

This handles a holdings/positions snapshot export (current value, cost
basis, and performance metrics per position), not transaction activity
- one row per security position. Maps to the brokerage_holdings table
per the KIND_BROKERAGE contract (see csv_statement.py's module
docstring).

Real export header columns:
  Account number, Account name, Symbol, Description, Quantity,
  Last price, Last price change, Current value, Today's gain/loss
  dollar, Today's gain/loss percent, Total gain/loss dollar,
  Total gain/loss percent, Percent of account, Cost basis total,
  Average cost basis, Type

Two columns need translating rather than a straight copy - see parse()'s
comments for why: Type is the sub-account (cash vs. margin), not an
asset type, so it maps to position_type, not type; and the core/sweep
money-market position is marked by a "**" suffix on its own symbol
("SPAXX**"), which becomes is_cash_equivalent instead.
"""

from institutions import common
from parser_common import KIND_BROKERAGE

KIND = KIND_BROKERAGE

_INSTITUTION = "Fidelity Investments"

_REQUIRED_HEADER = {
    "Account number",
    "Account name",
    "Symbol",
    "Description",
    "Quantity",
    "Last price",
    "Last price change",
    "Current value",
    "Cost basis total",
    "Average cost basis",
    "Type",
    "Percent of account",
}


def detect(header: list[str], sample_rows: list[list[str]]) -> tuple[bool, str]:
    cols = set(header)
    missing = _REQUIRED_HEADER - cols
    if missing:
        return False, f"missing column(s) {sorted(missing)}"
    return True, "Fidelity Portfolio Positions holdings snapshot header matched"


def _amount_or_none(raw: str) -> float | None:
    # Fidelity prints "--" for a field that doesn't apply to a given
    # position (e.g. cost basis on a cash sweep line).
    if not raw or raw == "--":
        return None
    return common.parse_amount(raw)


def parse(rows: list[dict[str, str]], csv_path: str) -> dict:
    holdings = []
    for row in rows:
        # The export ends with footer disclaimer text stored as extra
        # rows rather than real positions - these have no real account
        # number, so filter on the plain-language fragments Fidelity
        # prints there.
        account = (row.get("Account number") or "").strip()
        if "The data" in account or "Brokerage services" in account or "Date downloaded" in account:
            continue

        # This export names its own core/sweep money-market position by
        # suffixing the symbol with "**" (e.g. "SPAXX**") - that is the
        # one reliable cash-equivalent signal the file gives, so it is
        # stripped off the symbol (the real ticker has no "**" and
        # cross-source joins on symbol need the clean form) and turned
        # into is_cash_equivalent instead.
        symbol = (row.get("Symbol") or "").strip()
        is_cash_equivalent = symbol.endswith("**")
        if is_cash_equivalent:
            symbol = symbol[:-2]

        # "Type" here is NOT the security's asset type (stock, fund,
        # option, ...) - the export has no column for that at all. It is
        # the sub-account a position is held in: "Cash" for the default
        # (non-margin) sub-account, blank for an account that cannot
        # hold a margin position at all (e.g. a 401(k)). That is exactly
        # what brokerage_holdings.position_type means elsewhere (see
        # institutions/fidelity_brokerage.py, which sets it to "Margin"
        # or leaves it unset) - "Cash"/blank both collapse to unset here
        # so the two sources agree on the default case. Mapping it to
        # `type` instead, as an earlier version of this parser did,
        # mislabeled every single holding - stocks, funds, real cash
        # alike - as asset_class "Cash" once securities.go picked it up.
        raw_position_type = (row.get("Type") or "").strip()
        position_type = "" if raw_position_type in ("", "Cash") else raw_position_type

        percent_raw = (row.get("Percent of account") or "").strip().replace("%", "")
        holding = {
            "account": account,
            "accountType": (row.get("Account name") or "").strip(),
            "symbol": symbol,
            "description": (row.get("Description") or "").strip(),
            "quantity": _amount_or_none((row.get("Quantity") or "").strip()),
            "price": _amount_or_none((row.get("Last price") or "").strip()),
            "current_value": _amount_or_none((row.get("Current value") or "").strip()),
            "cost_basis_total": _amount_or_none((row.get("Cost basis total") or "").strip()),
            "average_cost_basis": _amount_or_none((row.get("Average cost basis") or "").strip()),
            "percent_of_account": _amount_or_none(percent_raw),
        }
        if position_type:
            holding["position_type"] = position_type
        if is_cash_equivalent:
            holding["is_cash_equivalent"] = True
        holdings.append(holding)

    common.tag_account(holdings, "", "Brokerage")

    return {
        "institution": _INSTITUTION,
        "statementDate": "",
        "tables": {"brokerage_holdings": holdings},
    }
