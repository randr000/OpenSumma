"""Agents the benchmark evaluates.

An agent is anything with a ``name`` and a ``run`` method: given a task's prompt and
the tools, it works through the tools and returns its answer as JSON values. It sees
nothing else: not the database, not the dataset's ground truth.

Two agents come with the benchmark. ``null`` answers nothing, the floor every agent
should clear. ``oracle`` is not an agent being evaluated but a calibration: it runs
each task's reference solution, which knows the expected answer and reaches it
through the tools, to show every task can be solved through them and scored in
full. Other agents are named by import path, as ``package.module:attribute``.
"""

import importlib
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from opensumma.benchmark.schema import Instance, TaskPrompt
from opensumma.benchmark.tools import Tools


@runtime_checkable
class Agent(Protocol):
    name: str

    def run(self, task: TaskPrompt, tools: Tools) -> Mapping[str, Any]:
        """Work on ``task`` through ``tools`` and return the answer, as JSON values
        following the task's answer schema."""
        ...


class NullAgent:
    """Answers every task with nothing."""

    name = "null"

    def run(self, task: TaskPrompt, tools: Tools) -> Mapping[str, Any]:
        return {}


class OracleAgent:
    """Runs each task's reference solution: a calibration, not an agent under
    evaluation. It is the only agent given the expected answer."""

    name = "oracle"

    def run(self, task: TaskPrompt, tools: Tools) -> Mapping[str, Any]:
        raise TypeError("the oracle solves an instance, with solve()")

    def solve(self, instance: Instance, tools: Tools) -> Mapping[str, Any]:
        return instance.task.solve(instance, tools)


BUILT_IN: dict[str, type[Agent]] = {"null": NullAgent, "oracle": OracleAgent}


def load_agent(spec: str) -> Agent:
    """The agent named by ``spec``: a built-in name, or ``module:attribute``, where
    the attribute is an agent, or a class or function that makes one with no
    arguments."""
    if spec in BUILT_IN:
        return BUILT_IN[spec]()
    module_name, _, attribute = spec.partition(":")
    if not attribute:
        raise ValueError(
            f"no built-in agent {spec!r}; built-in agents are "
            f"{', '.join(sorted(BUILT_IN))}, and others are named module:attribute"
        )
    target = getattr(importlib.import_module(module_name), attribute)
    # A class has an agent's attributes too, so it is called like any factory.
    makes_agent = isinstance(target, type) or (
        callable(target) and not isinstance(target, Agent)
    )
    agent = target() if makes_agent else target
    if not isinstance(agent, Agent):
        raise TypeError(f"{spec} is not an agent: it needs a name and a run method")
    return agent
