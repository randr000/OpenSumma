"""Where a task runs: a copy of a dataset's books, with the actors who use them.

Every task gets its own copy of the books, so tasks never affect one another and
an agent's changes stay behind for inspection. Two actors are registered on it: the
agent being evaluated, with the permissions the task grants, and a clerk, a human
colleague who sets up whatever the task needs (a bill to record, an entry to
check) through the workflow before the agent starts.
"""

import json
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from opensumma.datasets import (
    BOOKS_FILE,
    GROUND_TRUTH_FILE,
    MANIFEST_FILE,
    write_dataset,
)
from opensumma.datasets.rng import Rng
from opensumma.db import create_engine
from opensumma.workflow import (
    Actor,
    ActorType,
    Permission,
    create_actor,
    get_actor,
    issue_api_key,
)

AGENT = "agent"
CLERK = "clerk"


@dataclass(frozen=True)
class DatasetFiles:
    """A generated dataset: its books, manifest, and ground truth."""

    directory: Path
    manifest: dict[str, Any]
    ground_truth: dict[str, Any]

    @property
    def books(self) -> Path:
        return self.directory / BOOKS_FILE

    @property
    def year(self) -> int:
        year: int = self.manifest["year"]
        return year

    @property
    def seed(self) -> int:
        seed: int = self.manifest["seed"]
        return seed

    def summary(self) -> dict[str, Any]:
        """What results say about the dataset they were measured on."""
        keys = ("company", "year", "seed", "transactions", "fingerprint")
        return {key: self.manifest[key] for key in keys}


def write_standard_dataset(directory: Path | str) -> DatasetFiles:
    """Generate the dataset the benchmark uses when it is given none: a
    1,000-transaction company, seed 42, with the default errors."""
    write_dataset(directory, company="benchmark", transactions=1000, seed=42)
    return load_dataset(directory)


def load_dataset(directory: Path | str) -> DatasetFiles:
    """The dataset in ``directory``, whose ground truth must be for its books."""
    directory = Path(directory)
    for name in (BOOKS_FILE, MANIFEST_FILE, GROUND_TRUTH_FILE):
        if not (directory / name).is_file():
            raise FileNotFoundError(f"{directory} holds no dataset: {name} is missing")
    manifest = json.loads((directory / MANIFEST_FILE).read_text(encoding="utf-8"))
    truth = json.loads((directory / GROUND_TRUTH_FILE).read_text(encoding="utf-8"))
    if manifest["fingerprint"] != truth["fingerprint"]:
        raise ValueError(f"the ground truth in {directory} is for other books")
    return DatasetFiles(directory, manifest, truth)


@dataclass(frozen=True)
class Workspace:
    """A task's copy of the books, and the key the agent acts with."""

    directory: Path
    agent_key: str

    @property
    def books(self) -> Path:
        return self.directory / BOOKS_FILE

    @property
    def url(self) -> str:
        return f"sqlite:///{self.books}"


def create_workspace(
    dataset: DatasetFiles, directory: Path, permissions: tuple[Permission, ...]
) -> Workspace:
    """Copy the books into ``directory`` and register the agent and the clerk."""
    directory.mkdir(parents=True)
    shutil.copyfile(dataset.books, directory / BOOKS_FILE)
    engine = create_engine(f"sqlite:///{directory / BOOKS_FILE}")
    try:
        with Session(engine) as session:
            create_actor(
                session,
                code=CLERK,
                name="Clerk",
                actor_type=ActorType.HUMAN,
                permissions=[Permission.READ_ONLY, Permission.PROPOSER],
            )
            agent = create_actor(
                session,
                code=AGENT,
                name="Agent under evaluation",
                actor_type=ActorType.AGENT,
                permissions=permissions,
            )
            key = issue_api_key(session, agent)
            session.commit()
    finally:
        engine.dispose()
    return Workspace(directory, key)


@dataclass
class Environment:
    """What a task sees while it sets itself up and scores an answer: the books of
    its workspace, the dataset's ground truth, and a random stream of its own."""

    session: Session
    dataset: DatasetFiles
    rng: Rng
    agent: Actor
    clerk: Actor

    @property
    def year(self) -> int:
        return self.dataset.year

    def errors(self, *types: str) -> list[dict[str, Any]]:
        """The injected errors of ``types``, as the ground truth records them."""
        return [e for e in self.dataset.ground_truth["errors"] if e["type"] in types]


@contextmanager
def open_environment(
    workspace: Workspace, dataset: DatasetFiles, task_id: str
) -> Iterator[Environment]:
    engine = create_engine(workspace.url)
    try:
        with Session(engine) as session:
            yield Environment(
                session=session,
                dataset=dataset,
                rng=Rng(dataset.seed, f"benchmark:{task_id}"),
                agent=get_actor(session, AGENT),
                clerk=get_actor(session, CLERK),
            )
    finally:
        engine.dispose()
