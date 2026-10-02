from collections import defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any


def currency_totals(receipts: Iterable[Mapping[str, Any]]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for receipt in receipts:
        currency = receipt.get("currency") or "EUR"
        totals[currency] += Decimal(str(receipt.get("total_amount") or 0))
    return dict(sorted(totals.items()))


def grouped_totals(
    receipts: Iterable[Mapping[str, Any]], field: str, default: str
) -> dict[str, dict[str, Decimal]]:
    groups = defaultdict(list)
    for receipt in receipts:
        groups[receipt.get(field) or default].append(receipt)
    return {name: currency_totals(rows) for name, rows in sorted(groups.items())}


def format_amount(amount: int | float | Decimal, currency: str = "EUR") -> str:
    return f"€{amount:.2f}" if currency == "EUR" else f"{currency} {amount:.2f}"


def format_totals(totals: Mapping[str, Decimal]) -> str:
    return (
        ", ".join(
            format_amount(amount, currency)
            for currency, amount in sorted(totals.items())
        )
        or "—"
    )
