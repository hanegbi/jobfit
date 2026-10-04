"""Test doubles shared by the test suite and the sample/benchmark scripts."""

from pydantic import BaseModel

from jobfit_agent.agent.models import Usage


class FakeLLM:
    """Returns canned objects by schema name. A list is consumed in order; its last item repeats."""

    name = "fake"

    def __init__(self, responses: dict[str, "BaseModel | list[BaseModel]"]):
        self.responses = {k: (list(v) if isinstance(v, list) else [v]) for k, v in responses.items()}
        self.calls: list[tuple[str, str]] = []

    def run(self, schema, system, user):
        self.calls.append((schema.__name__, user))
        queue = self.responses[schema.__name__]
        obj = queue.pop(0) if len(queue) > 1 else queue[0]
        return obj, Usage(input_tokens=len(user) // 4, output_tokens=10, seconds=0.01, model="fake")
