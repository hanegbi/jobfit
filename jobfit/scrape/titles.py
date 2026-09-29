"""Split a job title out of a listing card's text.

Career-page builders routinely wrap a whole job card - title, department,
location, employment type, seniority, an "Apply" CTA - in one <a>, so the
link's text reads "Senior MLOps Engineer Full-time Senior Tel Aviv". Stored
as a title that pollutes search (a title-only search for "Tel Aviv" matched
hundreds of jobs), scoring and the location fallback, which reads the city
out of the title when the job carries no location of its own.

Two independent recoveries, in order of trust:
  1. The job's OWN page states its title (schema.org JobPosting, <h1>,
     og:title). `authoritative_title` accepts one only when it is contained
     in the card text, so a page-wide heading ("Careers at X") can never
     replace a real title - the rule can trim, never substitute.
  2. `split_card_text` peels known metadata off the card text itself, for
     the listings whose job pages aren't fetched. It strips only what it
     recognizes and stops at the first thing it doesn't, so a title that
     ends in a real word ("Sales Associate") is left alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from jobfit import ats_fetchers, config

_SEGMENT_SPLIT_RE = re.compile(r"\s*[·•|]\s*|\n+")

# "intern", "internship" and "student" are deliberately absent: they end real
# titles ("Software Engineering Intern") rather than tagging them.
_EMPLOYMENT = r"full[\s-]?time|part[\s-]?time|temporary|permanent|contract|freelance"
_MODE = r"remote|hybrid|on[\s-]?site|in[\s-]?office"
# A whole segment of its own ("... · Management") is a card's level tag. As a
# trailing WORD only the unambiguous ones may go: "associate", "management",
# "mid" and "experienced" end real titles ("Sales Associate").
_SENIORITY = r"senior|junior|jr\.?|sr\.?|mid[\s-]level|mid|intermediate|entry[\s-]level|associate|experienced|management"
_TRAILING_SENIORITY = r"senior|junior|jr\.?|sr\.?|mid[\s-]level|mid|entry[\s-]level|intermediate"
_CTA = r"apply(\s+(now|today|here))?|(read|learn|view|see)\s+more|view\s+(job|role|position|details)|more\s+details"

# Countries and territories seen as a trailing location in card text. Cities
# are not listed (there is no bounded list of them) - a foreign city only
# gets trimmed when the job page states the title itself.
_COUNTRIES = (
    "israel|united states|united states of america|united kingdom|england|scotland|"
    "germany|france|spain|portugal|italy|ireland|netherlands|belgium|switzerland|austria|"
    "poland|ukraine|romania|bulgaria|hungary|czech republic|serbia|greece|turkey|cyprus|"
    "sweden|denmark|norway|finland|canada|mexico|brazil|argentina|australia|new zealand|"
    "india|china|japan|taiwan|singapore|south korea|thailand|vietnam|philippines|"
    "united arab emirates|dubai|saudi arabia|south africa|nigeria|kenya|egypt"
)
# Country codes are matched case-sensitively: lowercase "us" is the pronoun
# ("About Us", "get in touch with us"), uppercase "US" is the country. "IL" is
# absent on purpose - it is Israel's own code, and ATS boards emit it as a
# suffix on Israeli towns ("Tirat Carmel, IL").
_COUNTRY_CODES = r"USA?|U\.S\.A?\.?|UK|UAE"
_ISRAEL_RE = re.compile(r"(?i:israel|ישראל)|\bIL\b")

# "Office" only counts as a label when it carries a colon, so "Head of Office
# Operations" keeps its own word.
_LOCATION_LABEL_RE = re.compile(r"\s*(?:\blocations?\b\s*:?|\boffices?\b\s*:)\s*", re.I)
_HEBREW_RE = re.compile(r"[֐-׿]")


def _clean(text: str) -> str:
    return " ".join((text or "").replace("\xa0", " ").split()).strip(" ,;:-–—·|")


def _alias_pattern(alias: str) -> str:
    escaped = re.escape(alias).replace(r"\ ", r"[\s-]")
    # \b does not delimit Hebrew, so those aliases match bare.
    return escaped if _HEBREW_RE.search(alias) else rf"\b{escaped}\b"


# Longest alias first, so "tel aviv-yafo" wins over "tel aviv" and never
# leaves a stray "Yafo" behind.
_CITY = "|".join(
    _alias_pattern(alias)
    for alias in sorted(
        (a for city, aliases in config.CITY_ALIASES.items() if city != "israel" for a in aliases),
        key=len, reverse=True,
    )
)
_COUNTRY_SUFFIX = r"(?:[\s,-]+(?:israel|il|ישראל))?"

_ANY_CITY_RE = re.compile(rf"(?:{_CITY})", re.I)
_TRAILING_CITY_RE = re.compile(rf"[\s,·|-]*(?:{_CITY}){_COUNTRY_SUFFIX}$", re.I)
_LEADING_CITY_RE = re.compile(rf"^(?:il[\s,|-]+)?(?:{_CITY}){_COUNTRY_SUFFIX}[\s,·|-]*", re.I)

_BRACKETED_TAIL_RE = re.compile(r"[(\[][^()\[\]]*[)\]]$")

# What may be peeled off the END of a title, in the order it is tried. Each
# pattern matches its own leading separator so the strip leaves no debris.
_TRAILING_PATTERNS = (
    ("location", _TRAILING_CITY_RE),
    ("cta", re.compile(rf"[\s,·|-]*\b({_CTA})\s*[→>»]?$", re.I)),
    ("employment_type", re.compile(rf"[\s,·|-]*\b({_EMPLOYMENT})$", re.I)),
    ("work_mode", re.compile(rf"[\s,·|-]*\b({_MODE})$", re.I)),
    ("location", re.compile(rf"[\s,·|-]*\b({_COUNTRIES})$", re.I)),
    # A country CODE is only metadata after a separator ("Solutions Engineer -
    # USA"): bare "US" after a space is usually the pronoun ("ABOUT US").
    ("location", re.compile(rf"\s*[,·|-]\s*({_COUNTRY_CODES})$")),
    # No comma in the separator class: a comma makes the seniority part of the
    # title's own name ("Test Engineer, Senior"), not a card tag.
    ("seniority", re.compile(rf"(?<!,)(?<!,\s)[\s·|-]*\b({_TRAILING_SENIORITY})$", re.I)),
)


@dataclass(frozen=True)
class CardText:
    title: str
    location: str | None = None
    employment_type: str | None = None


def _segment_kind(segment: str) -> str | None:
    """'location'/'employment_type'/'work_mode'/'seniority'/'cta' when the
    WHOLE segment is that piece of metadata, else None (it carries the title)."""
    text = _clean(segment)
    for kind, pattern in (("employment_type", _EMPLOYMENT), ("seniority", _SENIORITY), ("work_mode", _MODE)):
        if re.fullmatch(pattern, text, re.I):
            return kind
    if re.fullmatch(rf"({_CTA})\s*[→>»]?", text, re.I):
        return "cta"
    if re.fullmatch(r"israel|ישראל", text, re.I) or re.fullmatch(_COUNTRY_CODES, text):
        return "location"
    stripped = _TRAILING_CITY_RE.sub("", _LOCATION_LABEL_RE.sub("", text)).strip(" ,·|-")
    if not stripped or re.fullmatch(_COUNTRIES, stripped, re.I) or re.fullmatch(_COUNTRY_CODES, stripped):
        return "location"
    return None


def _strip_trailing_metadata(title: str) -> tuple[str, dict[str, str]]:
    found: dict[str, str] = {}
    while title:
        # A bracketed tail is the title's own qualifier - "(Remote)",
        # "(50%)", "(DACH)" - never the card's metadata.
        if _BRACKETED_TAIL_RE.search(title):
            break
        label = _LOCATION_LABEL_RE.search(title)
        if label and label.start() > 0:
            value = _clean(title[label.end():])
            if value:
                found.setdefault("location", value)
                title = _clean(title[: label.start()])
                continue
        for kind, pattern in _TRAILING_PATTERNS:
            match = pattern.search(title)
            if not match or match.start() == 0:
                continue
            found.setdefault(kind, _clean(match.group()))
            title = _clean(title[: match.start()])
            break
        else:
            break
    return title, found


def split_card_text(text: str) -> CardText:
    """The title carried by a listing card's text, plus the location and
    employment type peeled off it."""
    segments = [_clean(s) for s in _SEGMENT_SPLIT_RE.split(_clean(text) or "") if _clean(s)]
    found: dict[str, str] = {}
    carries_title = []
    for segment in segments:
        kind = _segment_kind(segment)
        if kind is None:
            carries_title.append(segment)
        elif kind in ("location", "employment_type", "work_mode"):
            found.setdefault(kind, _clean(_LOCATION_LABEL_RE.sub("", segment)))
    # The longest remaining segment is the title: a card that leads with its
    # department ("Engineering | Senior Backend Engineer | Tel Aviv") would
    # otherwise be titled "Engineering". Anything sentence-length is a
    # description blurb, not a title.
    title = max((s for s in carries_title if len(s.split()) <= 12), key=lambda s: len(s.split()), default="")
    if not title and carries_title:
        title = carries_title[0]

    leading = _LEADING_CITY_RE.match(title)
    if leading and _clean(title[leading.end():]):
        found.setdefault("location", _clean(leading.group()))
        title = _clean(title[leading.end():])

    title, trailing = _strip_trailing_metadata(title)
    for kind, value in trailing.items():
        found.setdefault(kind, value)
    # A work mode is not a place: reporting "Hybrid" as the location made the
    # relevance check drop an Israeli job. Only "Remote" is worth carrying,
    # because downstream reads it as the location it effectively is.
    mode = found.get("work_mode")
    location = found.get("location") or (mode if mode and re.fullmatch(r"remote", mode, re.I) else None)
    return CardText(title=title, location=location, employment_type=found.get("employment_type"))


def names_foreign_country(text: str | None) -> bool:
    """True when a location names a country other than Israel and no Israeli
    city - "United States", "London, United Kingdom". Used to stop the
    company-address fallback from relabelling a foreign job as an Israeli one."""
    cleaned = _clean(text or "")
    if not cleaned or _ISRAEL_RE.search(cleaned) or _ANY_CITY_RE.search(cleaned):
        return False
    return bool(re.search(rf"\b({_COUNTRIES})\b", cleaned, re.I) or re.search(rf"\b({_COUNTRY_CODES})\b", cleaned))


def detail_title_candidates(html: str) -> list[str]:
    """Every title a job's own page states: schema.org JobPosting, <h1>s,
    og:title, and the document title with its site-name suffix split off."""
    try:
        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:  # noqa: BLE001 - malformed markup yields no candidates
        return []
    out = [_clean(str(p.get("title") or "")) for p in ats_fetchers._jsonld_job_postings(soup)]
    out += [_clean(h.get_text(" ")) for h in soup.find_all("h1")]
    og = soup.find("meta", property="og:title")
    if og is not None:
        out.append(_clean(og.get("content") or ""))
    if soup.title is not None:
        document_title = _clean(soup.title.get_text(" "))
        out.append(document_title)
        out += [_clean(part) for part in re.split(r"\s+[|–—]\s+|\s+-\s+", document_title)]
    return [t for t in out if t]


def _normalized(text: str) -> str:
    return " ".join(re.sub(r"[^0-9a-z֐-׿]+", " ", (text or "").lower()).split())


def authoritative_title(candidates: list[str], listing_title: str) -> str | None:
    """The longest candidate the card text contains - i.e. the card text with
    its metadata trimmed off. None when no candidate is contained in it, so a
    heading that belongs to the page rather than the job is never adopted.

    Whatever the candidate cuts from the FRONT must be recognized metadata (a
    card that leads with its city), never plain words: a page heading reading
    "Backend Engineer" must not turn the card's "Senior Backend Engineer" into
    a different, more junior role."""
    listing = _normalized(listing_title)
    if not listing:
        return None
    best = None
    for candidate in candidates:
        normalized = _normalized(candidate)
        if len(normalized) < 8 or len(normalized.split()) < 2 or normalized == listing:
            continue
        start = listing.find(normalized)
        if start < 0 or (best is not None and len(normalized) <= len(_normalized(best))):
            continue
        if start > 0 and _segment_kind(listing[:start]) not in ("location", "employment_type", "work_mode", "cta"):
            continue
        best = _clean(candidate)
    return best
