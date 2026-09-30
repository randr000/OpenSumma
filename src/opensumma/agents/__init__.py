"""Example accounting agents, which work through the semantic tools alone.

Three agents show how an agent is built on OpenSumma, and give the benchmark
baselines to measure other agents against:

- ``InvestigationAgent`` answers questions about the books and finds what is wrong
  in them: unusual charges, misclassified expenses, wrong tags and periods, bills
  at the wrong amount or without their vendor, receipts missing from the books,
  and missing accruals. It only reads.
- ``JournalEntryAgent`` records bills the way the company records that vendor's
  bills, validates entries, and corrects invalid ones, leaving its work for a
  person to approve.
- ``DuplicateInvoiceAgent`` finds vendor bills recorded, and bills paid, more than
  once. It only reads.

``ExampleAgents`` is the three as one, each task going to the agent that handles
it. None of them uses a language model: each applies explicit accounting rules to
what the books hold, so they are deterministic, and what they miss or mistake is
the rule's doing. They see what any agent connected to the MCP server sees, the
books through the tools, never the database or a dataset's ground truth, and every
change they make carries a concise reason and evidence, kept in the audit log.
"""

from opensumma.agents.common import Finding, ToolRefusedError, WorkflowAgent
from opensumma.agents.duplicates import DuplicateInvoiceAgent
from opensumma.agents.investigation import InvestigationAgent
from opensumma.agents.journal_entries import JournalEntryAgent
from opensumma.agents.team import ExampleAgents

__all__ = [
    "DuplicateInvoiceAgent",
    "ExampleAgents",
    "Finding",
    "InvestigationAgent",
    "JournalEntryAgent",
    "ToolRefusedError",
    "WorkflowAgent",
]
