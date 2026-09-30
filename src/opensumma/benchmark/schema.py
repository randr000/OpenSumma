"""The benchmark's task schema: what a task is, what an agent sees of it, and how an
answer is scored.

A ``Task`` is defined once. Prepared against a dataset's books, it becomes an
``Instance``: concrete instructions, such as which account and date to report on,
and the expected answer, both derived deterministically from the dataset and its
ground truth. An agent sees only the ``TaskPrompt``: the instructions and the JSON
schema its answer must follow. Its answer is scored by the task, against the
expected answer and the books as the agent left them, into a ``Score``.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints

from opensumma.workflow import Permission

if TYPE_CHECKING:
    from opensumma.benchmark.environment import Environment
    from opensumma.benchmark.tools import Tools

Amount = Annotated[str, StringConstraints(pattern=r"^-?\d+(\.\d{1,2})?$")]

# What a task's score can say besides its overall score, and so which of the
# benchmark's metrics the task contributes to.
NUMERICAL = "numerical"
CLASSIFICATION = "classification"
WORKFLOW = "workflow"


class Answer(BaseModel):
    """An agent's answer. Fields a task does not ask for are ignored; amounts are
    strings, as everywhere else."""

    model_config = ConfigDict(extra="ignore")


@dataclass(frozen=True)
class TaskPrompt:
    """What an agent is given: the task, and the form its answer takes."""

    id: str
    title: str
    instructions: str
    answer_schema: dict[str, Any]


@dataclass(frozen=True)
class Score:
    """How well an answer did, from 0 to 1, and the parts the metrics are built
    from. ``classified`` and ``correctly_classified`` count the answer's items that
    named a real error, and those that also gave the right correction."""

    score: float
    numerical: float | None = None
    classified: int = 0
    correctly_classified: int = 0
    workflow: float | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Prepared:
    """What preparing a task yields: its instructions for this dataset, the answer
    expected, and anything the reference solution and the scorer need to know,
    such as the ids of what the task set up."""

    instructions: str
    expected: Mapping[str, Any]
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Instance:
    """A task prepared against one dataset's books."""

    task: "Task"
    instructions: str
    expected: Mapping[str, Any]
    context: Mapping[str, Any] = field(default_factory=dict)

    @property
    def prompt(self) -> TaskPrompt:
        return TaskPrompt(
            id=self.task.id,
            title=self.task.title,
            instructions=self.instructions,
            answer_schema=self.task.answer_schema,
        )


@dataclass(frozen=True)
class Task:
    """A benchmark task.

    ``prepare`` derives an instance from the books, setting up anything the task
    needs through the workflow; ``score`` scores an answer against it; ``solve`` is
    the reference solution, which reaches the right answer through the tools, and
    shows the task can be solved with them.
    """

    id: str
    title: str
    category: str
    description: str
    answer: type[Answer]
    measures: frozenset[str]
    prepare: Callable[["Environment"], Prepared]
    score: Callable[[Instance, Answer, "Environment"], Score]
    solve: Callable[[Instance, "Tools"], dict[str, Any]]
    permissions: tuple[Permission, ...] = (Permission.READ_ONLY, Permission.PROPOSER)

    def instance(self, environment: "Environment") -> Instance:
        """The task prepared against ``environment``'s books."""
        prepared = self.prepare(environment)
        return Instance(
            self, prepared.instructions, prepared.expected, prepared.context
        )

    @property
    def answer_schema(self) -> dict[str, Any]:
        return self.answer.model_json_schema()

    def describe(self) -> dict[str, Any]:
        """The task as JSON: what it asks and measures, and its answer's schema."""
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "description": self.description,
            "measures": sorted(self.measures),
            "permissions": [permission.value for permission in self.permissions],
            "answer_schema": self.answer_schema,
        }


def dice(expected: set[Any], answered: set[Any]) -> float:
    """How far ``answered`` agrees with ``expected``: the F1 score of the items
    found. Two empty sets agree completely."""
    if not expected and not answered:
        return 1.0
    return 2 * len(expected & answered) / (len(expected) + len(answered))


def amount(text: str) -> Decimal:
    """An answered amount, to the cent, so ``"12.5"`` and ``"12.50"`` agree."""
    return Decimal(text).quantize(Decimal("0.01"))
