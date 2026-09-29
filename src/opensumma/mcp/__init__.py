"""The MCP interface: the semantic accounting tools over the Model Context Protocol.

``create_server`` builds the server, which acts as the actor whose API key it is
given. Run it with ``python -m opensumma.mcp``, which serves it over stdio, as MCP
hosts launch local servers. The domain layers never import this package or the MCP
SDK, and it never imports the REST interface.
"""

from opensumma.mcp.server import create_server

__all__ = ["create_server"]
