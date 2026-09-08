# Parser IO contract

Every parser under `scripts/institutions/` (PDF) and
`scripts/csv_institutions/` (CSV / `.xlsx`) exposes a `detect()` / `parse()`
pair. The dispatchers (`scripts/bank_statement.py`,
`scripts/csv_statement.py`) try each registered parser's `detect()` in
order, first match wins, then call that parser's `parse()`. The result is
strictly validated by `scripts/parser_common.py`
(`validate_parse_result` / `validate_multi_table_parse_result`) before it
is printed as JSON — an extra, missing, or wrong-typed key fails loudly
with a message naming the parser and field, never silently as zeroed
data. Orby re-checks the same shape on the Go side
(`json.Decoder.DisallowUnknownFields`).

## `detect()`

| dispatcher | signature |
|---|---|
| `bank_statement.py` (PDF) | `detect(head_text: str) -> tuple[bool, str]` |
| `csv_statement.py` (CSV/xlsx) | `detect(header: list[str], sample_rows: list[list[str]]) -> tuple[bool, str]` |

Returns `(matched, reason)`. `reason` is a short diagnostic — on a match
what was found, on a non-match why (which marker/column was missing) — so
a rejected document's output can explain why nothing recognized it. A
`detect()` that raises is treated as an ordinary non-match.

## `parse()` — `KIND_BANK` (the default)

Checking / savings / credit-card statements and CSV exports. A module
does **not** opt in; this is the default.

```python
def parse(pages_text: list[str], pdf_path: str, vision: dict | None) -> dict   # PDF
def parse(rows: list[dict[str, str]], path: str) -> dict                        # CSV
```

Result — **exactly** these keys:

| key | type | notes |
|---|---|---|
| `institution` | `str` | may be `""` |
| `statementDate` | `str` | `""` or `YYYY-MM-DD` |
| `transactions` | `list[dict]` | may be empty |
| `needsVisionOcr` | `bool` | optional, default `False`; PDF only — check images could be OCR'd but no `vision` was supplied |

There is **no** top-level `account` / `accountType` — one statement can
cover several accounts, so they live per transaction.

Each `transactions[]` entry — **exactly**:

| key | type | notes |
|---|---|---|
| `date` | `str` | `YYYY-MM-DD` |
| `description` | `str` | non-empty after `.strip()` |
| `amount` | `int`/`float` | signed — see below |
| `balance` | `int`/`float`/`None` | key required, value may be `null` |
| `account` | `str` | last 3–4 digits, or `""` |
| `accountType` | `str` | e.g. `Checking` / `Savings` / `Credit Card`, or `""` |
| `reference` | `str` | optional — the issuer's own reference number |
| `institution` | `str` | optional — only if it differs from the statement's primary |
| `provider_account_id` | `str` | optional — fullest account identifier disclosed |

`vision`, when not `None`, is `{"endpoint", "model", "api_key"}` for an
OpenAI-compatible vision-capable chat endpoint (`check_ocr.py` uses it to
read embedded check images). `pdf_path` is the original file for a parser
that must re-open the PDF itself. Most parsers ignore both.

## `parse()` — `KIND_BROKERAGE`

A brokerage statement commonly has several sections worth structured
data. The module sets `KIND = parser_common.KIND_BROKERAGE` at module
scope and returns a `tables` dict instead of `transactions`:

```python
KIND = parser_common.KIND_BROKERAGE
def parse(pages_text: list[str], pdf_path: str) -> dict   # no vision / needsVisionOcr
```

Result — **exactly** `institution` (`str`), `statementDate` (`str`),
`tables` (`dict[str, list[dict]]`). `tables` keys must be a subset of:

- **`cash_transactions`** — identical row shape to a `KIND_BANK`
  `transactions[]` row.
- **`brokerage_transactions`** — required: `date`, `description`,
  `amount`, `account`, `accountType`. Optional: `action`,
  `transaction_type`, `subtype`, `symbol`, `security_id`,
  `security_id_type`, `quantity`, `price`, `commission_and_fees`,
  `realized_gain`, `realized_gain_term`, `related_security_id`,
  `currency_code`, `transaction_time`, `status`, `reference`,
  `cancel_reference`, `provider_account_id`, `institution`.
- **`brokerage_holdings`** — required: `symbol`, `account`,
  `accountType`. Optional: `description`, `quantity`, `price`,
  `current_value`, `cost_basis_total`, `average_cost_basis`,
  `percent_of_account`, `estimated_annual_income`, `estimated_yield`,
  `type`, `subtype`, `security_id`, `security_id_type`, `cusip`, `isin`,
  `sedol`, `figi`, `currency_code`, `price_as_of`, `price_time`,
  `vested_quantity`, `vested_value`, `position_type`,
  `market_identifier_code`, `sector`, `industry`, `is_cash_equivalent`,
  `tax_lots_json`, `provider_account_id`, `institution`.

Any of the three tables may be omitted or empty.

A share movement with no cash behind it (merger, assignment, expiry,
share-lending adjustment) is a `brokerage_transactions` row with
`transaction_type = "corporate_action"`, `amount` `0.00`, the share
change in `quantity`, and — when the statement names the security on the
other side — its identifier in `related_security_id`.

`bank_statement.py` normalizes a `KIND_BANK` match into the same envelope
(`tables = {"cash_transactions": transactions}`) before printing, so
consumers see one output shape regardless of which kind matched.

## Amount sign convention

`amount` is signed by how the transaction affects the money the account
holder actually has, the same way across every account type — **not** by
how the issuing statement's own balance figure moves.

- **Negative** = poorer: withdrawals, fees, credit-card
  purchases/charges.
- **Positive** = richer: deposits, credit-card payments/credits.

For a credit-card parser, parse and validate in the statement's own
printed sign (charges positive, payments negative — check against
`Previous Balance + sum == New Balance`), then negate every `amount` and
`balance` as the final step (`common.negate_amounts_and_balances`).

## Shared helpers (`scripts/institutions/common.py`)

`parse_amount`, `last4_digits`, `apply_running_balance`,
`negate_amounts_and_balances`. Import from a `csv_institutions/` module
too with `from institutions import common`.
