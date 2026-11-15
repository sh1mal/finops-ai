import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import timedelta

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .extraction import ExtractedInvoice


class LookupUnavailable(Exception):
    pass


def lookup_records(fields: ExtractedInvoice, invoice_id: str) -> dict:
    try:
        return anyio.run(fetch_records, fields, invoice_id)
    except Exception as error:
        logging.getLogger(__name__).warning("MCP verification failed for invoice %s", invoice_id, exc_info=True)
        raise LookupUnavailable("Invoice records are temporarily unavailable. Resume processing to retry.") from error


@asynccontextmanager
async def record_session():
    # Keep API credentials out of the child process. Pass only database configuration.
    database_env = {
        key: value for key, value in os.environ.items()
        if key == "FINOPS_DATABASE_URL" or key in (
            "PGHOST", "PGHOSTADDR", "PGPORT", "PGUSER", "PGPASSWORD", "PGPASSFILE",
            "PGSERVICE", "PGSERVICEFILE", "PGSSLMODE", "PGSSLROOTCERT", "PGSSLCERT",
            "PGSSLKEY", "PGOPTIONS",
        )
    }
    params = StdioServerParameters(command=sys.executable, args=["-m", "backend.mcp_server"], env=database_env)
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=8)) as session:
            await session.initialize()

            yield session


async def read_record(session, tool: str, **arguments):
    result = await session.call_tool(tool, arguments)
    if result.isError or result.structuredContent is None or "record" not in result.structuredContent:
        raise LookupUnavailable(f"MCP lookup failed: {tool}")
    return result.structuredContent["record"]


async def fetch_records(fields: ExtractedInvoice, invoice_id: str) -> dict:
    async with record_session() as session:
        vendor = await read_record(session, "get_vendor", name=fields.vendor)
        order = await read_record(session, "get_purchase_order", number=fields.po)
        if not vendor:
            return {"vendor": None, "order": order, "agreement": None, "prior": None}
        agreement = await read_record(session, "get_vendor_agreement", vendor_id=vendor["id"], issued_on=fields.issued.isoformat())
        prior = await read_record(session, "find_prior_invoice", vendor_id=vendor["id"], number=fields.number, exclude_id=invoice_id)
        near = None
        if prior is None:
            near = await read_record(
                session, "find_near_duplicate", vendor_id=vendor["id"], number=fields.number,
                exclude_id=invoice_id, amount_cents=fields.amount, issued_on=fields.issued.isoformat(),
            )
        history = await read_record(session, "get_vendor_history", vendor_id=vendor["id"], before=fields.issued.isoformat())
        if history is None:
            raise LookupUnavailable("Vendor history lookup returned no summary.")
        return {"vendor": vendor, "order": order, "agreement": agreement, "prior": prior, "near": near, "history": history}
