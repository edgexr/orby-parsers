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

import pdfplumber
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
_ACTIVITY_LABELS = tuple(_TOTAL_LABELS) + ("Change In Market Value", "Ending Balance")
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


def _label_and_amounts(line: str) -> tuple[str, list[float]] | None:
    line = re.sub(r"(?<=\d)\$(?=\d)", " $", re.sub(r"\s+", " ", line.strip()))
    match = _SUMMARY_LABEL_RE.match(line)
    if not match:
        return None
    label, rest = match.groups()
    amounts = [_money(token) for token in _MONEY_RE.findall(rest)]
    if not amounts:
        return None
    return ("Revenue Credit" if label == "Fees" else label), amounts


def _split_activity_names(header_lines: list[str], width: int) -> list[str]:
    pieces = [re.sub(r"\bActivity\b", "", line).strip() for line in header_lines]
    pieces = [piece for piece in pieces if piece]
    if not pieces or width <= 0:
        return ["" for _ in range(width)]

    rows = [piece.split() for piece in pieces]
    if len(rows) == 1:
        words = rows[0]
        chunk = max(1, len(words) // width)
        return [" ".join(words[i * chunk : (i + 1) * chunk]).strip() for i in range(width)]

    # NetBenefits prints matrix headers as stacked text rows.  For the
    # common wrapped-name case, the first and last rows each carry one
    # fragment per investment, while any middle row contains fragments
    # that pdfplumber can only extract left-to-right.  Assign those
    # middle fragments to the shortest names, which matches the visual
    # columns without relying on this statement's specific fund names.
    names = ["" for _ in range(width)]
    for i, token in enumerate(rows[0][:width]):
        names[i] = token
    if len(rows[-1]) >= width:
        for i, token in enumerate(rows[-1][:width]):
            names[i] = f"{names[i]} {token}".strip()
        middle_rows = rows[1:-1]
    else:
        middle_rows = rows[1:]

    for row in middle_rows:
        for token in row:
            i = min(range(width), key=lambda idx: (len(names[idx].split()), idx))
            names[i] = f"{names[i]} {token}".strip()
    return names


def _make_activity_txn(statement_date: str, label: str, description: str, amount: float) -> dict | None:
    classification = _TOTAL_LABELS.get(label)
    if classification is None or abs(amount) <= _EPS:
        return None
    action, transaction_type, subtype = classification
    return {
        "date": statement_date,
        "description": f"{label} - {description or 'Unknown Investment'}",
        "amount": amount,
        "action": action,
        "transaction_type": transaction_type,
        "subtype": subtype,
        "symbol": _symbol(description) if description else "",
        "currency_code": "USD",
    }


def _parse_detailed_activity(lines: list[str], statement_date: str) -> list[dict]:
    txns: list[dict] = []
    header_lines: list[str] = []
    names: list[str] = []
    in_detail = False

    for raw_line in lines:
        line = re.sub(r"\s+", " ", raw_line.strip())
        if line == "Your Account Activity":
            in_detail = True
            header_lines = []
            names = []
            continue
        if not in_detail:
            continue
        if line.startswith("Revenue Credit represents") or line.startswith("Your Account Information"):
            break
        if not line or line.startswith("Statement Period:") or line.startswith("Use this section"):
            continue
        if line == "Detailed Transaction History" or line.startswith("https://"):
            continue

        parsed = _label_and_amounts(line)
        if parsed:
            label, amounts = parsed
            if not names or len(names) != len(amounts):
                names = _split_activity_names(header_lines, len(amounts))
            for description, amount in zip(names, amounts):
                txn = _make_activity_txn(statement_date, label, description, amount)
                if txn:
                    txns.append(txn)
            if label in {"Ending Balance", "Dividends & Interest"}:
                header_lines = []
                names = []
            continue

        if not _SUMMARY_LABEL_RE.match(line):
            header_lines.append(line)

    return txns


def _money_words(words: list[dict]) -> list[dict]:
    return [w for w in words if _MONEY_RE.fullmatch(w["text"])]


def _row_label(words: list[dict]) -> str | None:
    label_words = [w["text"] for w in words if w["x0"] < 225 and not _MONEY_RE.fullmatch(w["text"])]
    label = " ".join(label_words)
    for candidate in _ACTIVITY_LABELS:
        if label.startswith(candidate):
            return candidate
    return None


def _word_rows(page) -> list[tuple[float, list[dict]]]:
    grouped: list[tuple[float, list[dict]]] = []
    for word in page.extract_words(x_tolerance=2, y_tolerance=3):
        top = float(word["top"])
        for i, (row_top, words) in enumerate(grouped):
            if abs(row_top - top) <= 3:
                words.append(word)
                grouped[i] = (row_top, sorted(words, key=lambda w: w["x0"]))
                break
        else:
            grouped.append((top, [word]))
    return sorted(grouped, key=lambda item: item[0])


def _column_names(header_rows: list[tuple[float, list[dict]]], amount_words: list[dict]) -> list[str]:
    centers = [(w["x0"] + w["x1"]) / 2 for w in amount_words]
    buckets: list[list[tuple[float, float, str]]] = [[] for _ in centers]
    if not centers:
        return []

    for top, words in header_rows:
        if top < 25:
            continue
        if len(words) > 6 and words[0]["x0"] < 100:
            continue
        for word in words:
            text = word["text"]
            if text == "Activity" or text.startswith("http"):
                continue
            center = (word["x0"] + word["x1"]) / 2
            if center < min(centers) - 35 or _MONEY_RE.fullmatch(text):
                continue
            col = min(range(len(centers)), key=lambda i: abs(center - centers[i]))
            buckets[col].append((top, word["x0"], text))

    names = []
    for bucket in buckets:
        parts = [text for _, _, text in sorted(bucket)]
        name = re.sub(r"\s+", " ", " ".join(parts)).strip()
        names.append(name)
    return names


def _parse_detailed_activity_pdf(pdf_path: str, statement_date: str) -> list[dict]:
    txns: list[dict] = []
    try:
        pdf = pdfplumber.open(pdf_path)
    except Exception:  # noqa: BLE001 - text fallback below can still parse simple fixtures.
        return txns

    with pdf:
        for page in pdf.pages:
            rows = _word_rows(page)
            block_start = 0
            names: list[str] = []
            for i, (top, words) in enumerate(rows):
                label = _row_label(words)
                if not label:
                    continue
                amount_words = _money_words(words)
                if label == "Beginning Balance":
                    candidate_header_rows = rows[block_start:i]
                    activity_rows = [
                        offset
                        for offset, (_, row_words) in enumerate(candidate_header_rows)
                        if any(w["text"] == "Activity" for w in row_words)
                    ]
                    if activity_rows:
                        start = max(0, activity_rows[-1] - 2)
                        header_rows = candidate_header_rows[start:]
                    else:
                        header_rows = candidate_header_rows
                    if len(amount_words) < 2 or not any(w["text"] == "Activity" for _, row_words in header_rows for w in row_words):
                        names = []
                        continue
                    names = _column_names(header_rows, amount_words)
                    continue
                if not names:
                    continue
                for name, amount_word in zip(names, amount_words):
                    if name == "Total":
                        continue
                    txn = _make_activity_txn(statement_date, label, name, _money(amount_word["text"]))
                    if txn:
                        txns.append(txn)
                if label == "Ending Balance":
                    block_start = i + 1
                if label == "Dividends & Interest":
                    block_start = i + 1
                    names = []
    return txns


def _parse_activity(pages_text: list[str], pdf_path: str, statement_date: str) -> tuple[list[dict], dict[str, float]]:
    # Prefer the per-investment Account Activity page's Total column.  If
    # that page is absent, the first-page account summary uses "Fees" for
    # the same positive revenue-credit line.
    all_lines = [line for text in pages_text for line in text.splitlines()]
    totals = _activity_totals(all_lines)
    txns = _parse_detailed_activity_pdf(pdf_path, statement_date)
    if not txns:
        txns = _parse_detailed_activity(all_lines, statement_date)
    if txns:
        return txns, totals

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
    brokerage_transactions, activity_totals = _parse_activity(pages_text, pdf_path, statement_date)
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
