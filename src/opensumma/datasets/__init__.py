"""Deterministic accounting datasets: a company's books, with known errors in them.

``write_dataset`` generates a company's year of business into a directory: its
books, a manifest, and the ground truth of every error injected into them. The same
parameters always give the same dataset. ``generate_dataset`` does the same into a
session, and ``plan_dataset`` plans one without a database.

The generator is trusted code: it records through the kernel and the object layer,
beneath the workflow, so that it can record the mistakes the workflow's controls
exist to catch. It depends on the kernel, the object layer, and the workflow's
audit log, and on nothing above them.
"""

from opensumma.datasets.books import books_fingerprint, export_books
from opensumma.datasets.business import (
    DEFAULT_YEAR,
    FIXED_TRANSACTIONS,
    MAX_TRANSACTIONS,
    MIN_TRANSACTIONS,
)
from opensumma.datasets.errors import ERROR_TYPES, default_error_count
from opensumma.datasets.generator import (
    BOOKS_FILE,
    GENERATOR_VERSION,
    GROUND_TRUTH_FILE,
    MANIFEST_FILE,
    Dataset,
    DatasetPlan,
    generate_dataset,
    plan_dataset,
    record_dataset,
    write_dataset,
)

__all__ = [
    "BOOKS_FILE",
    "DEFAULT_YEAR",
    "ERROR_TYPES",
    "FIXED_TRANSACTIONS",
    "GENERATOR_VERSION",
    "GROUND_TRUTH_FILE",
    "MANIFEST_FILE",
    "MAX_TRANSACTIONS",
    "MIN_TRANSACTIONS",
    "Dataset",
    "DatasetPlan",
    "books_fingerprint",
    "default_error_count",
    "export_books",
    "generate_dataset",
    "plan_dataset",
    "record_dataset",
    "write_dataset",
]
