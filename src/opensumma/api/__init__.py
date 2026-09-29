"""The REST interface: FastAPI over the workflow and the kernel.

``create_app`` builds the application. Run it with ``python -m opensumma.api``, or
with ``uvicorn --factory opensumma.api:create_app``. The domain layers never import
this package or FastAPI.
"""

from opensumma.api.app import create_app

__all__ = ["create_app"]
