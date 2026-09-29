"""What the interfaces share: how records are presented, and the unit of work.

The REST interface (``opensumma.api``) and the MCP interface (``opensumma.mcp``) are
thin adapters over the same workflow and kernel. Both present an account, an entry,
or a report through ``views``, as the models in ``schemas``, so an agent reads the
same JSON whichever interface it uses; and both commit an action through ``work``,
so a refused action keeps its audit event on either.

This package depends on the domain layers and on Pydantic, never on FastAPI or the
MCP SDK, and neither interface depends on the other.
"""
