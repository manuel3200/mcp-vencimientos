"""
account_tools.py - Fachada de compatibilidad hacia mcp_server.tools.account_tools (Q06).
Evita registrar dos veces los decoradores @mcp.tool sobre la instancia FastMCP.
"""
from mcp_server.tools.account_tools import *  # noqa: F401,F403
