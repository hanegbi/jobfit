"""One clean English name for a job's department.

ATS boards hand back whatever the hiring company typed: 378 distinct values
across 2,700 jobs, including 'R&D', 'RND', 'RnD', 'R & D' and 'Engineering '
as five different departments, Hebrew for seven more, and internal codes
('348-RAT', 'IC 3', 'ISL - Meta - MEPMS - (Project Delivery)') that mean
nothing outside the company that wrote them. A facet built on that is
unusable, so every value is folded into one fixed English vocabulary.

**A department names a function.** Not a level ('ניהול' - management, 'IC 3'),
not a programme ('סטודנטים' - students, 'Freelancers'), not a place
('Tel-Aviv Office', 'Back Office'), and not a catch-all for everything that
is not the product ('תפקידי מטה' - staff roles, 'G&A'). Those all become
None: no department, which is honest, rather than a department that tells
you nothing about the work. The raw string stays in the scrape's own record.
"""

from __future__ import annotations

import re

# The whole vocabulary. Engineering is split the way engineering job boards
# actually split it, because "Engineering" alone would be 40% of every job
# with a department and answer nothing.
SOFTWARE = "Software Engineering"
DEVOPS = "DevOps & Infrastructure"
HARDWARE = "Hardware & Embedded"
QA = "QA & Automation"
DATA_AI = "Data & AI"
SECURITY = "Security"
IT = "IT"
PRODUCT = "Product"
DESIGN = "Design"
SALES = "Sales"
MARKETING = "Marketing"
CUSTOMER = "Customer Success"
OPERATIONS = "Operations"
FINANCE = "Finance"
HR = "HR"
LEGAL = "Legal"
MANUFACTURING = "Manufacturing"

CANONICAL = (
    SOFTWARE, DEVOPS, HARDWARE, QA, DATA_AI, SECURITY, IT, PRODUCT, DESIGN,
    SALES, MARKETING, CUSTOMER, OPERATIONS, FINANCE, HR, LEGAL, MANUFACTURING,
)

# Hebrew departments, translated by hand. A fixed vocabulary needs no
# translation service and must not depend on one being reachable. The three
# mapped to None are a level, a programme and a catch-all, not functions.
HEBREW = {
    "הנדסה ופיתוח": SOFTWARE,      # engineering & development
    "הנדסאים/טכנאים": HARDWARE,     # practical engineers / technicians
    "ייצור": MANUFACTURING,         # production
    "ניהול": None,                  # management - a level
    "סטודנטים": None,               # students - a programme
    "תפקידי מטה": None,             # HQ/staff roles - a catch-all
    "אחר": None,                    # other
}

# Explicitly not a department: a level, a programme, a place, or a bundle of
# everything that is not the product. Anchored, so only the bare value is
# excluded - 'Sales Management' is still Sales, and 'CEO Office' is still
# nothing. Checked before the rules, so 'Management' never reaches a keyword
# that would lend it a function it does not have.
#
# There is no rule here for internal codes ('348-RAT', 'IQCC'): a value that
# matches no keyword already ends up as None, and a rule shaped like a code
# is one IGNORECASE away from matching every one-word department there is.
_NOT_A_DEPARTMENT = re.compile(
    r"^(management|general|general ?& ?administrative|g ?& ?a|admin(istration)?"
    r"|(\w+ )*(back |front )?office|corporate|strategy|executive|territory management"
    r"|students?|interns?(hips?)?|graduates?|freelancers?|other|misc\w*)$",
    re.IGNORECASE,
)

# Ordered: the first pattern that matches wins, so the specific ones come
# before the general. "Product Security" is Security, not Product; "Sales
# Engineering" is Sales, not Software; "Cloud Ops" is infrastructure, not
# business operations.
_RULES: tuple[tuple[str, str], ...] = (
    (r"security|cyber|ciso|threat|infosec|trust & safety|fraud", SECURITY),
    (r"\bsales\b|\bsdr\b|revenue|go.?to.?market|\bgtm\b|business development"
     r"|channels?\b|partner|commercial|account executive|new business", SALES),
    (r"marketing|brand|demand gen|advertis|communications|growth", MARKETING),
    (r"customer|client|\bcx\b|\bcs\b|support|success|member care"
     r"|professional services|onboarding", CUSTOMER),
    # "Validation" is laboratory work in a lab and software QA in software -
    # the word is shared, the job is not. Guarding the QA rule rather than
    # routing lab work somewhere of its own: 'Drug Discovery and Validation'
    # then matches nothing and gets no department, which is the honest answer.
    (r"^(?!.*\b(drug|clinical|pharma|bio\w*)\b).*"
     r"(quality assurance|\bqa\b|\bsqa\b|\bautomation\b|validation|verification"
     r"|\bv ?& ?v\b|\bsdet\b)", QA),
    (r"devops|\bsre\b|site reliability|infrastructure|\binfra\b|cloud ?ops"
     r"|platform ops|production engineering|\bnoc\b", DEVOPS),
    (r"hardware|embedded|firmware|mechanical|electrical|electronics|silicon"
     r"|\bvlsi\b|\basic\b|\brf\b|optics|photonics|technicians?", HARDWARE),
    (r"\bdata\b|\bai\b|\bml\b|machine learning|analytics|\bbi\b|algorithm"
     r"|insight|research|intelligence|science", DATA_AI),
    (r"product manage|^product$|product ops|product operation|product deliver"
     r"|product solution|product growth|product technology", PRODUCT),
    (r"design|\bux\b|\bui\b|creative", DESIGN),
    (r"\bit\b|information system|helpdesk|service desk|business system"
     r"|business application", IT),
    (r"legal|compliance|audit|regulatory|privacy", LEGAL),
    (r"finance|account(ing|s)?\b|payroll|treasury|\brisk\b|\btax\b|billing"
     r"|reconciliation", FINANCE),
    (r"people|human resource|\bhr\b|recruit|talent|culture", HR),
    (r"manufactur|production|supply chain|logistics|warehouse|quality", MANUFACTURING),
    (r"engineering|\br ?& ?d\b|\brnd\b|software|\bdev\b|development|platform"
     r"|technolog|\btech\b|architecture|\bcto\b|solutions?\b|programming", SOFTWARE),
    (r"operations?|\bops\b|delivery|project management|program management"
     r"|business operations|operational excellence", OPERATIONS),
)
_COMPILED = tuple((re.compile(pattern, re.IGNORECASE), canonical) for pattern, canonical in _RULES)


def canonical_department(raw: str | None) -> str | None:
    """The department this value belongs to, or None when it names no
    function - a level, a programme, a place, an internal code, or a
    catch-all for everything that is not the product."""
    text = " ".join((raw or "").split())
    if not text:
        return None
    if text in HEBREW:
        return HEBREW[text]
    if _NOT_A_DEPARTMENT.match(text):
        return None
    for pattern, canonical in _COMPILED:
        if pattern.search(text):
            return canonical
    return None


# The role families the scorer already classifies titles into, as departments.
# Reusing that curated vocabulary rather than writing a second one: it is
# maintained for scoring, and two title vocabularies would drift apart.
# 'management' is deliberately absent - a team lead is a level, not a function,
# and the title says nothing about which function they lead.
_FAMILY_TO_DEPARTMENT = {
    "backend": SOFTWARE, "frontend": SOFTWARE, "fullstack": SOFTWARE, "mobile": SOFTWARE,
    "data_engineering": DATA_AI, "data_science": DATA_AI, "data_analytics": DATA_AI,
    "ml_engineering": DATA_AI,
    "ml_infra": DEVOPS, "devops": DEVOPS,
    "security": SECURITY, "qa": QA, "embedded": HARDWARE,
    "product": PRODUCT, "design": DESIGN, "sales": SALES, "support": CUSTOMER,
    "marketing": MARKETING, "finance": FINANCE, "hr": HR, "legal": LEGAL,
    "operations": OPERATIONS,
}


def department_for(title: str | None, raw: str | None = None) -> str | None:
    """A job's department: what its title says it does, falling back to what
    the ATS said.

    The title wins, which is not the obvious way round. A stated department
    is the company's org chart - whose budget the role sits in - while the
    title describes the work. Where the two disagree (302 open jobs), the
    title is right almost every time: "Senior DevOps Engineer" and "Senior
    Data Engineer" both filed under Software Engineering, "Product Security
    Engineer" and "Senior Product Designer" both under Product, "Customer
    Success" under Sales. Someone searching for work filters on the work.

    The stated value still fills the silence: only 2,338 open jobs are
    classified by title alone, and 1,377 only by what the company said.
    """
    if title:
        from jobfit.ats_scorer.taxonomy import load_role_families

        family = load_role_families().classify(title)
        by_title = _FAMILY_TO_DEPARTMENT.get(family or "")
        if by_title is not None:
            return by_title
    return canonical_department(raw)
