"""Regex patterns shared by the job-description and CV extractors, so both
sides classify degrees/certifications/languages/locations/dates the same
way."""

import re

MONTH_RE_FRAGMENT = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)

DEGREE_RE = re.compile(
    r"\b(b\.?sc\.?|bachelor'?s?(?:\s+degree)?|m\.?sc\.?|master'?s?(?:\s+degree)?|ph\.?d\.?|"
    r"doctorate|degree in [a-zA-Z\s]+)\b",
    re.IGNORECASE,
)
CERTIFICATION_RE = re.compile(
    r"\b([\w]+(?:\s[\w]+){0,3}\s+certifi(?:cation|ed)|certified\s[\w]+(?:\s[\w]+){0,3}|"
    r"aws certified[\w\s]*|cissp|ccna|ccnp|pmp|ckad|cka|oscp)\b",
    re.IGNORECASE,
)
LANGUAGE_RE = re.compile(
    r"\b(fluent (?:in )?[a-zA-Z]+|native (?:speaker of )?[a-zA-Z]+|"
    r"(english|hebrew|spanish|french|german|arabic|mandarin|russian)\s+(?:speaker|proficiency|required|fluency))\b",
    re.IGNORECASE,
)
LOCATION_RE = re.compile(
    r"\b(on-?site|hybrid|must be (?:located|based) in [\w\s,]+|"
    r"relocation to [\w\s,]+|based in [\w\s,]+)\b",
    re.IGNORECASE,
)

YEARS_TOTAL_RE = re.compile(
    r"(\d{1,2})\s*\+?\s*years?\s+(?:of\s+)?(?:professional\s+|relevant\s+|overall\s+)?experience\b|"
    r"(?:at least|minimum(?:\s+of)?|over)\s*(\d{1,2})\s*years?\s+(?:of\s+)?experience\b|"
    r"(\d{1,2})\s*-\s*\d{1,2}\s*years?\s+(?:of\s+)?experience\b",
    re.IGNORECASE,
)

DATE_RANGE_RE = re.compile(
    rf"((?:{MONTH_RE_FRAGMENT}\.?\s+)?\d{{4}})\s*(?:[-–—]|to)\s*"
    rf"((?:{MONTH_RE_FRAGMENT}\.?\s+)?\d{{4}}|present|current|now|ongoing)",
    re.IGNORECASE,
)
