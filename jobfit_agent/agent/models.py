"""The one seam between the graph and a language model.

Nodes call get_llm(node).run(schema, system, user) and get back a validated
pydantic object plus Usage. Tests replace get_llm with a FakeLLM.
"""

import time
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from jobfit_agent.agent import config


class LLMParseError(RuntimeError):
    pass


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    model: str = ""


class LLM(Protocol):
    def run(self, schema: type[BaseModel], system: str, user: str) -> tuple[BaseModel, Usage]: ...


def cost_entry(node: str, usage: Usage) -> dict:
    return {"node": node, "model": usage.model, "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens, "seconds": usage.seconds}


def parse_model_spec(spec: str) -> tuple[str, str]:
    provider, _, name = spec.partition(":")
    return provider, name


class ChatLLM:
    """Wraps a langchain chat model; structured output with one retry."""

    def __init__(self, chat, name: str):
        self.chat = chat
        self.name = name

    def run(self, schema, system, user):
        structured = self.chat.with_structured_output(schema, include_raw=True)
        messages = [("system", system), ("human", user)]
        started = time.perf_counter()
        last_error = None
        for _ in range(2):
            out = structured.invoke(messages)
            if out.get("parsed") is not None:
                meta = getattr(out.get("raw"), "usage_metadata", None) or {}
                return out["parsed"], Usage(
                    input_tokens=meta.get("input_tokens", 0), output_tokens=meta.get("output_tokens", 0),
                    seconds=round(time.perf_counter() - started, 2), model=self.name)
            last_error = out.get("parsing_error")
        raise LLMParseError(f"{self.name} did not return valid {schema.__name__}: {last_error}")


def get_llm(node: str) -> LLM:
    spec = config.NODE_MODELS[node]
    provider, name = parse_model_spec(spec)
    if provider == "ollama":
        from langchain_ollama import ChatOllama
        # num_ctx: Ollama defaults to 4096 and silently drops the rest of the prompt,
        # which on a long job description is the whole requirements section.
        return ChatLLM(ChatOllama(model=name, temperature=0, num_ctx=config.NUM_CTX,
                                  reasoning=config.REASONING), spec)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatLLM(ChatAnthropic(model=name, temperature=0, max_tokens=4096), spec)
    raise ValueError(f"unknown provider {provider!r} for node {node!r} (use ollama: or anthropic:)")
