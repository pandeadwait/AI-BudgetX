"""PII stripping. Everything crossing to a provider goes through here first."""

import re

PATTERNS: dict[str, re.Pattern] = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    "upi": re.compile(r"\b[\w.-]{2,}@(?:ok\w+|paytm|ybl|upi|axl|ibl)\b", re.I),
    "phone": re.compile(r"(?:\+91[-\s]?)?\b\d{10}\b"),
    "card": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    "account": re.compile(r"\b\d{9,18}\b"),
    "ifsc": re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b"),
}


def redact(text: str | None) -> tuple[str, list[str]]:
    """Returns the scrubbed text and the names of the patterns that matched."""
    if not text:
        return "", []
    stripped = []
    # UPI handles look like emails, so email must not eat them first.
    for name in ("upi", "email", "ifsc", "card", "phone", "account"):
        pattern = PATTERNS[name]
        if pattern.search(text):
            text = pattern.sub(f"[{name.upper()}_REDACTED]", text)
            stripped.append(name)
    return text, stripped


if __name__ == "__main__":
    cases = [
        ("paid advait@okhdfc 500", "upi"),
        ("mail me at a.b+x@gmail.com", "email"),
        ("call 9876543210 later", "phone"),
        ("card 4111 1111 1111 1111", "card"),
        ("dinner with friends", None),
    ]
    for text, expected in cases:
        clean, found = redact(text)
        assert (expected in found) if expected else not found, (text, clean, found)
        assert expected is None or "REDACTED" in clean, clean
    print("redact ok")
