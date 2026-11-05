"""ISIN = "US" + CUSIP9 + check digit (ISO 6166, Luhn over the letter-expanded string)."""

from __future__ import annotations


def isin_check_digit(body: str) -> str:
    digits = "".join(str(int(c, 36)) for c in body.upper())
    total = 0
    for i, ch in enumerate(reversed(digits)):
        v = int(ch)
        if i % 2 == 0:
            v *= 2
            if v > 9:
                v -= 9
        total += v
    return str((10 - total % 10) % 10)


def isin_from_cusip9(cusip9: str, country: str = "US") -> str:
    if len(cusip9) != 9:
        raise ValueError(f"CUSIP9 must have 9 characters, got {cusip9!r}")
    body = country + cusip9.upper()
    return body + isin_check_digit(body)


def is_valid_isin(isin: str) -> bool:
    return len(isin) == 12 and isin_check_digit(isin[:11]) == isin[11]
