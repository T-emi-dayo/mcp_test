import os
from dotenv import load_dotenv
from core.logger import logger
from fastmcp.server.auth import StaticTokenVerifier


# ── Environment & auth ────────────────────────────────────────────────────────
load_dotenv()

tokens = {}
for key, value in os.environ.items():
    if key.startswith("MCP_AGENT_TOKEN_"):
        agent_name = key.replace("MCP_AGENT_TOKEN_", "").lower()
        tokens[value] = {
            "client_id": agent_name,
            "scopes": ["admin"] if agent_name == "admin" else ["read", "write"]
        }

if not tokens:
    # This won't stop the server, but it will warn you loudly at startup
    # that something is wrong with your env config.
    logger.warning("No agent tokens loaded. Check MCP_AGENT_TOKEN_* env vars.")
else:
    logger.info("Auth configured", extra={"token_count": len(tokens)})

auth = StaticTokenVerifier(tokens=tokens)