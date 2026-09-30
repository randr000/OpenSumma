"""The benchmark: accounting tasks, run by an agent through the tools, scored exactly.

``run_benchmark`` runs an agent on every task against a generated dataset and
writes the results: scores, metrics, and every tool call the agent made. Tasks and
their scoring are deterministic, and no answer is judged by a model; expected
answers come from the books and the dataset's ground truth.

The benchmark sits above the datasets and the MCP interface: agents act through the
same semantic tools any MCP client uses, as a registered actor, and every change they
make is audited.
"""

from opensumma.benchmark.agents import Agent, NullAgent, OracleAgent, load_agent
from opensumma.benchmark.environment import DatasetFiles, load_dataset
from opensumma.benchmark.runner import (
    BENCHMARK_VERSION,
    RESULTS_CSV,
    RESULTS_FILE,
    TRAJECTORIES,
    WORKSPACES,
    BenchmarkResult,
    TaskResult,
    Usage,
    run_benchmark,
)
from opensumma.benchmark.schema import Answer, Instance, Score, Task, TaskPrompt
from opensumma.benchmark.tasks import TASKS, get_task
from opensumma.benchmark.tools import ToolCall, Tools, ToolSpec

__all__ = [
    "BENCHMARK_VERSION",
    "RESULTS_CSV",
    "RESULTS_FILE",
    "TASKS",
    "TRAJECTORIES",
    "WORKSPACES",
    "Agent",
    "Answer",
    "BenchmarkResult",
    "DatasetFiles",
    "Instance",
    "NullAgent",
    "OracleAgent",
    "Score",
    "Task",
    "TaskPrompt",
    "TaskResult",
    "ToolCall",
    "ToolSpec",
    "Tools",
    "Usage",
    "get_task",
    "load_agent",
    "load_dataset",
    "run_benchmark",
]
