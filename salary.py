"""Salary: parse it from the admin JD form and format it for display.

A JD stores salary as four optional fields (all None when not provided):

    salary_min       int | None   e.g. 1200000
    salary_max       int | None   e.g. 1800000
    salary_currency  str | None   "INR" | "USD" | "EUR" | "GBP"
    salary_period    str | None   "year" | "month"

Keeping numbers (not free text) means salaries can later be sorted/filtered.
Older JDs have none of these keys; that is read as "not disclosed".
"""
from __future__ import annotations

from typing import Mapping, Optional

CURRENCIES = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}
PERIODS = {"year": "/ yr", "month": "/ mo"}
DEFAULT_CURRENCY = "INR"
DEFAULT_PERIOD = "year"

EMPTY_SALARY = {"salary_min": None, "salary_max": None,
                "salary_currency": None, "salary_period": None}


def _compact(amount: int, currency: str) -> str:
    """Short form of an amount: INR uses lakh/crore (12L, 1.2Cr), others K/M."""
    def trim(x: float) -> str:
        return f"{x:.1f}".rstrip("0").rstrip(".")

    if currency == "INR":
        if amount >= 10_000_000:
            return f"{trim(amount / 10_000_000)}Cr"
        if amount >= 100_000:
            return f"{trim(amount / 100_000)}L"
    if amount >= 1_000_000:
        return f"{trim(amount / 1_000_000)}M"
    if amount >= 1_000:
        return f"{trim(amount / 1_000)}K"
    return str(amount)


def format_salary(jd: Mapping) -> Optional[str]:
    """Return a display string like "₹12L – ₹18L / yr", or None if not disclosed.

    Args:
        jd: A JD dict (or anything with .get) holding the salary_* fields.
    """
    lo, hi = jd.get("salary_min"), jd.get("salary_max")
    if lo is None and hi is None:
        return None

    currency = jd.get("salary_currency") or DEFAULT_CURRENCY
    symbol = CURRENCIES.get(currency, currency + " ")
    period = PERIODS.get(jd.get("salary_period") or DEFAULT_PERIOD, "")

    def money(v: int) -> str:
        return f"{symbol}{_compact(int(v), currency)}"

    if lo is not None and hi is not None:
        text = money(lo) if lo == hi else f"{money(lo)} – {money(hi)}"
    elif lo is not None:
        text = f"From {money(lo)}"
    else:
        text = f"Up to {money(hi)}"
    return f"{text} {period}".strip()


def _parse_amount(raw: str) -> Optional[int]:
    """'12,00,000' / '1200000' / '' → int or None. Raises ValueError if invalid."""
    cleaned = (raw or "").replace(",", "").replace(" ", "").strip()
    if not cleaned:
        return None
    value = int(float(cleaned))
    if value < 0:
        raise ValueError("negative")
    return value


def parse_salary_form(form: Mapping) -> tuple[dict, Optional[str]]:
    """Read salary fields from a submitted JD form.

    Args:
        form: request.form (or any mapping) with salary_min, salary_max,
              salary_currency, salary_period.

    Returns:
        (fields, error). ``fields`` is ready to store on the JD. When both
        amounts are blank every field is None. ``error`` is a message for the
        admin, or None when the input is valid.
    """
    try:
        lo = _parse_amount(form.get("salary_min", ""))
        hi = _parse_amount(form.get("salary_max", ""))
    except ValueError:
        return dict(EMPTY_SALARY), "Salary must be a positive number (e.g. 1200000)."

    if lo is None and hi is None:
        return dict(EMPTY_SALARY), None
    if lo is not None and hi is not None and hi < lo:
        return dict(EMPTY_SALARY), "Maximum salary can't be lower than minimum salary."

    currency = form.get("salary_currency") or DEFAULT_CURRENCY
    period = form.get("salary_period") or DEFAULT_PERIOD
    if currency not in CURRENCIES or period not in PERIODS:
        return dict(EMPTY_SALARY), "Please choose a valid salary currency and period."

    return {"salary_min": lo, "salary_max": hi,
            "salary_currency": currency, "salary_period": period}, None
