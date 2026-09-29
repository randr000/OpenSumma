"""Serve the REST interface: ``python -m opensumma.api [--host H] [--port P]``."""

import argparse

import uvicorn

from opensumma.api import create_app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m opensumma.api", description="Serve the OpenSumma REST API."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--database-url",
        help="defaults to OPENSUMMA_DATABASE_URL, then sqlite:///opensumma.db",
    )
    args = parser.parse_args(argv)
    uvicorn.run(create_app(args.database_url), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
