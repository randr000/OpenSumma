"""Serve the MCP tools over stdio: ``python -m opensumma.mcp [--database-url U]``.

The server acts as the actor whose API key is in ``OPENSUMMA_API_KEY``. The key is
read from the environment rather than the command line, where other users of the
machine could see it in the process list.
"""

import argparse
import os

from opensumma.mcp import create_server

API_KEY_VARIABLE = "OPENSUMMA_API_KEY"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m opensumma.mcp",
        description="Serve the OpenSumma accounting tools over MCP, on stdio, as "
        f"the actor whose API key is in {API_KEY_VARIABLE}.",
    )
    parser.add_argument(
        "--database-url",
        help="defaults to OPENSUMMA_DATABASE_URL, then sqlite:///opensumma.db",
    )
    args = parser.parse_args(argv)
    api_key = os.environ.get(API_KEY_VARIABLE, "").strip()
    if not api_key:
        parser.error(f"set {API_KEY_VARIABLE} to the API key of the actor to act as")
    create_server(args.database_url, api_key=api_key).run("stdio")


if __name__ == "__main__":
    main()
