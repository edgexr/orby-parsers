# SPDX-License-Identifier: Apache-2.0
"""Fidelity NetBenefits 401(k) retirement savings statement parser.

This format is exported from the NetBenefits "Statement Details" page,
not Fidelity's ordinary monthly brokerage "INVESTMENT REPORT".  The PDF
prints an ending holdings snapshot and aggregate activity totals for the
statement period.  It does not print itemized transaction history even
though the page includes a "Detailed Transaction History" heading, so
the parser returns only the reliably printed aggregate activity rows.
"""

import re
from datetime import datetime

import parser_common
from institutions import common

KIND = parser_common.KIND_BROKERAGE

_INSTITUTION = "Fidelity NetBenefits"
_ACCOUNT_TYPE = "401(k)"
_EPS = 0.01

_PERIOD_RE = re.compile(
    r"Statement Period:\s*(\d{2})/(\d{2})/(\d{4})\s+to\s+(\d{2})/(\d{2})/(\d{4})"
)
_ACCOUNT_RE = re.compile(r"\bAccount(?: Number| #)?:\s*([A-Za-z0-9Xx*#-]+)", re.I)
_MONEY_RE = re.compile(r"-?\$[\d,]+\.\d{2}")
_HOLDING_ROW_RE = re.compile(
    r"^(?P<description>.*?)\s+"
    r"(?P<begin_qty>[\d,]+\.\d{3})\s+"
    r"(?P<end_qty>[\d,]+\.\d{3})\s+"
    r"\$(?P<begin_price>[\d,]+\.\d{2})\s+"
    r"\$(?P<end_price>[\d,]+\.\d{2})\s+"
    r"\$(?P<begin_value>[\d,]+\.\d{2})\s+"
    r"\$(?P<end_value>[\d,]+\.\d{2})$"
)

_SKIP_LINES_RE = re.compile(
    r"^(?:"
    r"\d{1,2}/\d{1,2}/\d{2},.*Fidelity NetBenefits"
    r"|https://.*"
    r"|Statement Details"
    r"|Shares/Units.*"
    r"|Price as of.*"
    r"|Market Value.*"
    r"|Investment as of.*"
    r"|\d{2}/\d{2}/\d{4}.*"
    r"|Remember that.*"
    r"|so a decrease.*"
    r"|fund performance.*"
    r")$",
    re.I,
)
_CATEGORY_RE = re.compile(
    r"^(?:"
    r"CORE OPTIONS AND ORACLE STK FD"
    r"|INDEX FUNDS \(PASSIVELY MANAGED\)"
    r"|Stock|International|Mid-Cap|Large Cap|Company Stock|Bond|Income"
    r")(?:\s+\$[-\d,.]+\s+\$[-\d,.]+)?$",
    re.I,
)

_TOTAL_LABELS = {
    "Beginning Balance": None,
    "Exchange In": ("Transfer In", "internal_transfer", "transfer"),
    "Exchange Out": ("Transfer Out", "internal_transfer", "transfer"),
    "Revenue Credit": ("Revenue Credit", "income", "other_income"),
    "Dividends & Interest": ("Dividend/Interest", "dividend", "interest"),
}
_SUMMARY_LABEL_RE = re.compile(
    r"^(Beginning Balance|Exchange In|Exchange Out|Fees|Revenue Credit|Change In Market Value|Ending Balance|Dividends & Interest)\s+(.+)$"
)


def detect(head_text: str) -> tuple[bool, str]:
    lower = head_text.lower()
    if "fidelity netbenefits" not in lower:
        return False, "'fidelity netbenefits' not found"
    if "retirement savings statement" not in lower:
        return False, "'retirement savings statement' not found"
    if not _PERIOD_RE.search(head_text):
        return False, "no NetBenefits statement period found"
    return True, "matched Fidelity NetBenefits retirement savings statement"


def _money(token: str) -> float:
    return common.parse_amount(token.replace("$", ""))


def _statement_date(text: str) -> str:
    match = _PERIOD_RE.search(text)
    if not match:
        return ""
    mm, dd, yyyy = match.group(4), match.group(5), match.group(6)
    try:
        return datetime.strptime(f"{mm}/{dd}/{yyyy}", "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _account(text: str) -> str:
    match = _ACCOUNT_RE.search(text)
    return common.last4_digits(match.group(1)) if match else ""


def _symbol(description: str) -> str:
    symbol = re.sub(r"[^A-Z0-9]+", " ", description.upper()).strip()
    return re.sub(r"\s+", " ", symbol)


def _is_noise(line: str) -> bool:
    return not line or bool(_SKIP_LINES_RE.match(line) or _CATEGORY_RE.match(line))


def _is_name_suffix(line: str) -> bool:
    return bool(re.match(r"^(?:Fd|GR|I|Tier|Worldwide|Pool(?:\s+Class|\s+CL)\s+\S+)$", line))


def _parse_holdings(pages_text: list[str]) -> tuple[list[dict], float | None]:
    holdings: list[dict] = []
    in_section = False
    pending_prefix = ""
    awaiting_suffix = False
    account_total = None
    statement_date = _statement_date("\n".join(pages_text))

    for text in pages_text:
        for raw_line in text.splitlines():
            line = re.sub(r"\s+", " ", raw_line.strip())
            if line == "Market Value of Your Account":
                in_section = True
                continue
            if not in_section:
                continue

            if line.startswith("Account Totals"):
                amounts = [_money(token) for token in _MONEY_RE.findall(line)]
                if len(amounts) >= 2:
                    account_total = amounts[-1]
                in_section = False
                pending_prefix = ""
                awaiting_suffix = False
                continue

            match = _HOLDING_ROW_RE.match(line)
            if match:
                description = match.group("description").strip()
                first_word = description.split(" ", 1)[0] if description else ""
                if pending_prefix and first_word in {"Common", "Index", "Market", "Smid", "Stock"}:
                    description = f"{pending_prefix} {description}".strip()
                pending_prefix = ""
                holding = {
                    "symbol": _symbol(description),
                    "description": description,
                    "quantity": common.parse_amount(match.group("end_qty")),
                    "price": common.parse_amount(match.group("end_price")),
                    "current_value": common.parse_amount(match.group("end_value")),
                    "currency_code": "USD",
                    "price_as_of": statement_date,
                }
                holdings.append(holding)
                awaiting_suffix = True
                continue

            if _is_noise(line):
                pending_prefix = ""
                awaiting_suffix = False
                continue

            if awaiting_suffix and holdings and _is_name_suffix(line):
                holdings[-1]["description"] = f"{holdings[-1]['description']} {line}".strip()
                holdings[-1]["symbol"] = _symbol(holdings[-1]["description"])
                awaiting_suffix = False
            else:
                pending_prefix = line
                awaiting_suffix = False

    return [h for h in holdings if abs(h.get("current_value", 0.0)) > _EPS], account_total


def _activity_totals(lines: list[str]) -> dict[str, float]:
    totals = {}
    for raw_line in lines:
        line = re.sub(r"(?<=\d)\$(?=\d)", " $", re.sub(r"\s+", " ", raw_line.strip()))
        match = _SUMMARY_LABEL_RE.match(line)
        if not match:
            continue
        label, rest = match.groups()
        amounts = [_money(token) for token in _MONEY_RE.findall(rest)]
        if amounts:
            normalized = "Revenue Credit" if label == "Fees" else label
            totals[normalized] = amounts[-1]
    return totals


def _parse_activity(pages_text: list[str], statement_date: str) -> tuple[list[dict], dict[str, float]]:
    # Prefer the per-investment Account Activity page's Total column.  If
    # that page is absent, the first-page account summary uses "Fees" for
    # the same positive revenue-credit line.
    all_lines = [line for text in pages_text for line in text.splitlines()]
    totals = _activity_totals(all_lines)
    txns = []
    for label, classification in _TOTAL_LABELS.items():
        if classification is None or label not in totals or abs(totals[label]) <= _EPS:
            continue
        action, transaction_type, subtype = classification
        txns.append(
            {
                "date": statement_date,
                "description": f"Statement-period {label}",
                "amount": totals[label],
                "action": action,
                "transaction_type": transaction_type,
                "subtype": subtype,
                "currency_code": "USD",
            }
        )
    return txns, totals


def _validate(holdings: list[dict], holding_total: float | None, activity_totals: dict[str, float]) -> None:
    if holding_total is not None:
        parsed_total = round(sum(h.get("current_value", 0.0) for h in holdings), 2)
        if abs(parsed_total - holding_total) > _EPS:
            raise ValueError(
                f"holdings current value total {parsed_total:.2f} does not match printed Account Totals {holding_total:.2f}"
            )

    required = {"Beginning Balance", "Exchange In", "Exchange Out", "Revenue Credit", "Change In Market Value", "Ending Balance"}
    missing = sorted(required - set(activity_totals))
    if missing:
        raise ValueError(f"missing account activity total(s): {missing}")
    expected_end = round(
        activity_totals["Beginning Balance"]
        + activity_totals["Exchange In"]
        + activity_totals["Exchange Out"]
        + activity_totals["Revenue Credit"]
        + activity_totals["Change In Market Value"],
        2,
    )
    if abs(expected_end - activity_totals["Ending Balance"]) > _EPS:
        raise ValueError(
            f"activity totals reconcile to {expected_end:.2f}, not printed Ending Balance {activity_totals['Ending Balance']:.2f}"
        )


def parse(pages_text: list[str], pdf_path: str) -> dict:
    text = "\n".join(pages_text)
    statement_date = _statement_date(text)
    account = _account(text)

    holdings, holding_total = _parse_holdings(pages_text)
    brokerage_transactions, activity_totals = _parse_activity(pages_text, statement_date)
    _validate(holdings, holding_total, activity_totals)

    common.tag_account(holdings, account, _ACCOUNT_TYPE)
    common.tag_account(brokerage_transactions, account, _ACCOUNT_TYPE)

    return {
        "institution": _INSTITUTION,
        "statementDate": statement_date,
        "tables": {
            "brokerage_holdings": holdings,
            "brokerage_transactions": brokerage_transactions,
        },
    }
