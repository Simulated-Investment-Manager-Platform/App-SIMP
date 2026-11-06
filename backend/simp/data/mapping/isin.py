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


def cusip_check_digit(cusip8: str) -> str:
    total = 0
    for i, ch in enumerate(cusip8.upper()):
        if ch.isdigit():
            v = int(ch)
        elif ch.isalpha():
            v = ord(ch) - 55
        else:
            v = {"*": 36, "@": 37, "#": 38}.get(ch, 0)
        if i % 2 == 1:
            v *= 2
        total += v // 10 + v % 10
    return str((10 - total % 10) % 10)


def cusip9_of(cusip9: str | None, cusip: str | None) -> str | None:
    """The 9-character CUSIP from CRSP's cusip9, else cusip (8 chars + check digit), else None."""
    for c in (cusip9, cusip):
        c = (c or "").strip().upper()
        if len(c) == 9:
            return c
        if len(c) == 8:
            return c + cusip_check_digit(c)
    return None


def is_valid_isin(isin: str) -> bool:
    return len(isin) == 12 and isin_check_digit(isin[:11]) == isin[11]


# CRSP primary exchange codes -> MIC of the primary listing (OPOL). MICs pass through unchanged.
EXCH_TO_MIC = {
    "N": "XNYS",
    "Q": "XNAS",
    "A": "XASE",
    "P": "ARCX",
    "R": "ARCX",  # CIZ code for NYSE Arca
    "Z": "BATS",
    "B": "BATS",  # CIZ code for Cboe BZX
    "I": "IEXG",
}


def exch_to_mic(code: str | None) -> str:
    if not code:
        return "XNYS"
    return EXCH_TO_MIC.get(code.strip().upper(), code.strip().upper())
