from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterator

from pydantic import ValidationError

from jobfit.atomic_io import write_json_atomic
from jobfit.scrape.models import SCRAPE_PLAN_SCHEMA_VERSION, ScrapePlan

logger = logging.getLogger("jobfit.scrape.plans")


class PlanStore(ABC):
    @abstractmethod
    def get(self, company_id: str) -> ScrapePlan | None: ...

    @abstractmethod
    def put(self, plan: ScrapePlan) -> None: ...

    @abstractmethod
    def all(self) -> Iterator[ScrapePlan]: ...


class FilePlanStore(PlanStore):
    def __init__(self, directory: Path):
        self.directory = directory

    def path_for(self, company_id: str) -> Path:
        return self.directory / f"{company_id}.json"

    def _load(self, path: Path) -> ScrapePlan | None:
        try:
            plan = ScrapePlan.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, ValidationError) as error:
            logger.warning("unreadable plan %s: %s", path.name, error)
            return None
        if plan.schema_version < SCRAPE_PLAN_SCHEMA_VERSION:
            plan = plan.model_copy(update={"status": "stale"})
        return plan

    def get(self, company_id: str) -> ScrapePlan | None:
        path = self.path_for(company_id)
        return self._load(path) if path.exists() else None

    def put(self, plan: ScrapePlan) -> None:
        write_json_atomic(self.path_for(plan.company_id), plan.model_dump(mode="json"))

    def all(self) -> Iterator[ScrapePlan]:
        if not self.directory.exists():
            return
        for path in sorted(self.directory.glob("*.json")):
            plan = self._load(path)
            if plan is not None:
                yield plan


class MemoryPlanStore(PlanStore):
    def __init__(self):
        self._plans: dict[str, ScrapePlan] = {}

    def get(self, company_id: str) -> ScrapePlan | None:
        return self._plans.get(company_id)

    def put(self, plan: ScrapePlan) -> None:
        self._plans[plan.company_id] = plan

    def all(self) -> Iterator[ScrapePlan]:
        for key in sorted(self._plans):
            yield self._plans[key]
