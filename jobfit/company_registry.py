"""One company, one id. Resolves a display name (and optionally a
career-page URL) to a stable company id through exactly one function
(resolve), so nothing else in the codebase invents its own ad hoc
name-matching. Backed by jobfit/data/company_registry.json, seeded
read-only from whatever companies/*.json files already exist the first
time it's loaded.

This module does NOT merge existing duplicate company files - that's a
separate, higher-risk migration (see the design spec, section 5) because
it re-keys every job id and needs a localStorage migration in the SPA.
What this module DOES do, today: register_if_new refuses to let a NEW
company file be created when it would collide (by normalized name, by
career-page host, or by a looser name key) with a DIFFERENT company that
already exists - so the ~110-170 duplicate pairs already in the corpus
don't keep growing while the real migration is designed and reviewed.

Real bug this is scoped around: 'monday.com' and 'Monday.com Ltd.
(Formerly DaPulse)' are the same real company, split into two files
because they entered the pipeline from two different sources
(a curated career-page URL and techmap's own company registry) with two
different spellings, and nothing checked whether a "new" company was
actually new.
"""

import json
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from jobfit import connections
from jobfit.atomic_io import write_json_atomic

_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")
_DOMAIN_SUFFIX_RE = re.compile(r"\.(com|io|ai|co)\b", re.IGNORECASE)


def _loose_key(name: str) -> str:
    """A looser match than connections.normalize_company alone: also drops
    parenthetical asides ("(Formerly DaPulse)") and common domain suffixes
    that sometimes leak into a company's display name."""
    stripped = _PARENTHETICAL_RE.sub(" ", name or "")
    stripped = _DOMAIN_SUFFIX_RE.sub(" ", stripped)
    return connections.normalize_company(stripped)


def _host_of(url: str | None) -> str | None:
    if not url:
        return None
    host = urlsplit(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


@dataclass
class CompanyEntry:
    id: str
    display_name: str
    career_url: str | None
    host: str | None


class DuplicateCompany(RuntimeError):
    def __init__(self, name: str, attempted_id: str, conflict_id: str):
        self.name = name
        self.attempted_id = attempted_id
        self.conflict_id = conflict_id
        super().__init__(
            f"refusing to create a new company file {attempted_id!r} for {name!r}: "
            f"it looks like the same company as the existing {conflict_id!r} "
            "(same normalized name or career-page host). "
            f"Check jobfit/companies/{conflict_id}.json - if this really is a "
            "different company, this gate has no manual override yet (that "
            "lands with the full registry migration)."
        )


class CompanyRegistry:
    def __init__(self, path: Path, entries: dict[str, CompanyEntry]):
        self.path = path
        self._entries = entries
        self._by_key: dict[str, str] = {}
        self._by_host: dict[str, str] = {}
        self._by_loose_key: dict[str, str] = {}
        for entry in entries.values():
            self._index(entry)
        self._lock = threading.Lock()

    def _index(self, entry: CompanyEntry) -> None:
        key = connections.normalize_company(entry.display_name)
        if key:
            self._by_key.setdefault(key, entry.id)
        if entry.host:
            self._by_host.setdefault(entry.host, entry.id)
        loose = _loose_key(entry.display_name)
        if loose:
            self._by_loose_key.setdefault(loose, entry.id)

    @classmethod
    def load(cls, registry_path: Path, companies_dir: Path) -> "CompanyRegistry":
        if registry_path.exists():
            data = json.loads(registry_path.read_text(encoding="utf-8"))
            entries = {e["id"]: CompanyEntry(**e) for e in data.get("companies", [])}
            return cls(registry_path, entries)
        registry = cls._seed_from_company_files(registry_path, companies_dir)
        registry.save()
        return registry

    @classmethod
    def _seed_from_company_files(cls, registry_path: Path, companies_dir: Path) -> "CompanyRegistry":
        entries: dict[str, CompanyEntry] = {}
        if companies_dir.exists():
            for path in sorted(companies_dir.glob("*.json")):
                if path.name == "_meta.json":
                    continue
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                career_url = record.get("career_url")
                entries[path.stem] = CompanyEntry(
                    id=path.stem, display_name=record.get("name", path.stem),
                    career_url=career_url, host=_host_of(career_url),
                )
        return cls(registry_path, entries)

    def save(self) -> None:
        write_json_atomic(self.path, {
            "schema_version": 1,
            "companies": [asdict(e) for e in self._entries.values()],
        })

    def resolve(self, name: str, url: str | None = None) -> str | None:
        key = connections.normalize_company(name)
        if key and key in self._by_key:
            return self._by_key[key]
        host = _host_of(url)
        if host and host in self._by_host:
            return self._by_host[host]
        loose = _loose_key(name)
        if loose and loose in self._by_loose_key:
            return self._by_loose_key[loose]
        return None

    def register_if_new(self, company_id: str, display_name: str, career_url: str | None) -> str | None:
        """If company_id is not yet registered, register it unless doing so
        would collide with a DIFFERENT existing entry - in which case,
        return that entry's id without registering anything. Returns None
        on success (including when company_id was already registered)."""
        with self._lock:
            if company_id in self._entries:
                return None
            conflict = self.resolve(display_name, career_url)
            if conflict is not None:
                return conflict
            entry = CompanyEntry(company_id, display_name, career_url, _host_of(career_url))
            self._entries[company_id] = entry
            self._index(entry)
            self.save()
            return None

    def check_invariants(self) -> list[str]:
        problems = []
        seen_keys: dict[str, str] = {}
        seen_hosts: dict[str, str] = {}
        for entry in self._entries.values():
            key = connections.normalize_company(entry.display_name)
            if key:
                if key in seen_keys and seen_keys[key] != entry.id:
                    problems.append(f"{entry.id!r} and {seen_keys[key]!r} share normalized name key {key!r}")
                else:
                    seen_keys.setdefault(key, entry.id)
            if entry.host:
                if entry.host in seen_hosts and seen_hosts[entry.host] != entry.id:
                    problems.append(f"{entry.id!r} and {seen_hosts[entry.host]!r} share career-page host {entry.host!r}")
                else:
                    seen_hosts.setdefault(entry.host, entry.id)
        return problems


_registry_cache: dict[Path, CompanyRegistry] = {}
_cache_lock = threading.Lock()


def get_registry(companies_dir: Path, registry_path: Path) -> CompanyRegistry:
    """Path-keyed cache: a distinct registry_path always gets its own
    instance, so tests (each using a distinct tmp_path) never share state
    with each other or with production, while production - which always
    derives the same registry_path from the real COMPANIES_DIR - only
    pays the seed-from-disk cost once per process."""
    with _cache_lock:
        if registry_path not in _registry_cache:
            _registry_cache[registry_path] = CompanyRegistry.load(registry_path, companies_dir)
        return _registry_cache[registry_path]
