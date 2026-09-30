"""One clean English name for a job's department.

ATS boards hand back whatever the hiring company typed: 378 distinct values
across 2,664 jobs, including 'R&D', 'RND', 'RnD', 'R & D' and 'Engineering '
as five different departments, Hebrew for seven more, and internal codes
('348-RAT', 'IC 3', 'ISL - Meta - MEPMS - (Project Delivery)') that mean
nothing outside the company that wrote them. A facet built on that is
unusable, so every value is folded into a small fixed English vocabulary.

Anything that matches nothing becomes None - no department, rather than a
department named after someone's internal cost centre. That loses no
information worth filtering on: the raw string stays in the job's row.
"""

from __future__ import annotations

import re

# The whole vocabulary. Deliberately short: a filter with 15 options is a
# filter; one with 378 is a list.
ENGINEERING = "Engineering"
DATA_AI = "Data & AI"
SECURITY = "Security"
PRODUCT = "Product"
DESIGN = "Design"
SALES = "Sales"
MARKETING = "Marketing"
CUSTOMER = "Customer Success"
OPERATIONS = "Operations"
FINANCE = "Finance"
PEOPLE = "People & HR"
LEGAL = "Legal"
IT = "IT"
MANUFACTURING = "Manufacturing"
STUDENTS = "Students & Internships"
GENERAL = "General & Admin"

CANONICAL = (
    ENGINEERING, DATA_AI, SECURITY, PRODUCT, DESIGN, SALES, MARKETING, CUSTOMER,
    OPERATIONS, FINANCE, PEOPLE, LEGAL, IT, MANUFACTURING, STUDENTS, GENERAL,
)

# Hebrew departments, translated by hand. A fixed seven-value vocabulary needs
# no translation service, and must not depend on one being reachable.
HEBREW = {
    "הנדסה ופיתוח": ENGINEERING,
    "הנדסאים/טכנאים": ENGINEERING,
    "ייצור": MANUFACTURING,
    "סטודנטים": STUDENTS,
    "ניהול": GENERAL,
    "תפקידי מטה": GENERAL,
    "אחר": None,
}

# Ordered: the first pattern that matches wins, so the specific ones come
# before the general. "Product Security" is Security, not Product; "Sales
# Engineer" is Sales, not Engineering.
_RULES: tuple[tuple[str, str | None], ...] = (
    (r"security|cyber|ciso|threat|infosec|trust & safety", SECURITY),
    (r"\bsales\b|\bsdr\b|revenue|go.?to.?market|\bgtm\b|business development|channels?\b"
     r"|partner|commercial|account executive|\bnew business\b", SALES),
    (r"marketing|brand|growth|demand gen|communications", MARKETING),
    (r"customer|client|\bcx\b|\bcs\b|support|success|member care|professional services", CUSTOMER),
    (r"\bdata\b|\bai\b|\bml\b|machine learning|analytics|\bbi\b|algorithm|insight|research"
     r"|intelligence", DATA_AI),
    (r"product manage|^product$|product ops|product deliver|product solution|product growth", PRODUCT),
    (r"design|\bux\b|\bui\b|creative", DESIGN),
    (r"engineering|\br ?& ?d\b|\brnd\b|^r&d|software|\bdev\b|devops|development|platform"
     r"|infrastructure|hardware|embedded|\bqa\b|technolog|\btech\b|architecture"
     r"|\bcto\b|validation|verification|solutions?\b", ENGINEERING),
    (r"\bit\b|information system|helpdesk|business system|business application", IT),
    (r"people|human resource|\bhr\b|recruit|talent|culture", PEOPLE),
    (r"legal|compliance|audit|regulatory", LEGAL),
    (r"finance|account|payroll|treasury|\brisk\b|tax\b", FINANCE),
    (r"manufactur|production|supply chain|logistics|quality|operational excellence", MANUFACTURING),
    (r"operations?|\bops\b|delivery|project management|program management", OPERATIONS),
    (r"student|intern(ship)?s?\b|graduate|junior program", STUDENTS),
    (r"general|admin|corporate|\bg ?& ?a\b|executive|management|strategy|office", GENERAL),
)
_COMPILED = tuple((re.compile(pattern, re.IGNORECASE), canonical) for pattern, canonical in _RULES)

def canonical_department(raw: str | None) -> str | None:
    """The department this value belongs to, or None when it names nothing a
    person would filter by - an internal code ('348-RAT', 'IC 3'), a product
    name, or an empty string."""
    text = " ".join((raw or "").split())
    if not text:
        return None
    if text in HEBREW:
        return HEBREW[text]
    for pattern, canonical in _COMPILED:
        if pattern.search(text):
            return canonical
    return None
