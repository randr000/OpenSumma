"""The three example agents as one agent, for running them on the whole benchmark."""

from typing import Any

from opensumma.agents.common import WorkflowAgent
from opensumma.agents.duplicates import DuplicateInvoiceAgent
from opensumma.agents.investigation import InvestigationAgent
from opensumma.agents.journal_entries import JournalEntryAgent
from opensumma.benchmark import TaskPrompt, Tools


class ExampleAgents:
    """Gives each task to the example agent that handles it, and answers nothing to
    a task none of them handles."""

    name = "examples"

    def __init__(self) -> None:
        self.agents: tuple[WorkflowAgent, ...] = (
            InvestigationAgent(),
            JournalEntryAgent(),
            DuplicateInvoiceAgent(),
        )

    def run(self, task: TaskPrompt, tools: Tools) -> dict[str, Any]:
        for agent in self.agents:
            if agent.handles(task.id):
                return agent.run(task, tools)
        return {}
