"""CPF display helpers (privacy). Plain functions, no Streamlit, so they can be tested on their own.
The stored value is never changed: these only build what is shown."""

import re


def cpf_digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def mask_cpf_last2(value) -> str:
    """Privacy display of a CPF: only the two check digits, e.g. ***.***.***-25. Never more than two digits,
    whatever the input looks like."""
    digits = cpf_digits(value)
    return f"***.***.***-{digits[-2:]}" if len(digits) >= 2 else "***.***.***-**"


def format_cpf_full(value) -> str:
    digits = cpf_digits(value)
    return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}" if len(digits) == 11 else str(value)
