"""One-off: attribute knowledge sources to past answers that have none.

Answers given in full-context mode before source attribution existed have an empty `sources`
list, so they show no "мэдлэг" link in the history and do not count in per-document usage.
This applies the same rule as the live chat (closest document by search, plus a practically tied
runner-up) to those turns. Safe to re-run: only turns with empty sources are touched.

    cd backend
    python -m scripts.backfill_sources            # dry run: prints what would change
    python -m scripts.backfill_sources --apply
"""

import argparse
import asyncio
import json
import sys

from app.config import get_settings
from app.container import build_container
from app.orchestrator.graph import ATTRIBUTION_SKIP_INTENTS, attribute_sources


async def main(apply: bool) -> int:
    s = get_settings()
    if not s.database_url:
        print("DATABASE_URL is not set: nothing to backfill (in-memory storage)")
        return 1
    c = await build_container(s)
    pool = c.services.repo.pool
    async with pool.connection() as conn:
        rows = await (await conn.execute(
            "SELECT id, persona, user_text FROM turns WHERE jsonb_array_length(sources) = 0 "
            "AND COALESCE(stop, 'end') = 'end' AND COALESCE(intent, '') <> ALL(%s)", (list(ATTRIBUTION_SKIP_INTENTS),),
        )).fetchall()
    changed = 0
    for turn_id, persona, text in rows:
        docs = await attribute_sources(c.services.kb, text, persona)
        if not docs:
            continue
        print(f"{turn_id}  {persona:8}  {text[:50]!r:55} -> {docs}")
        if apply:
            async with pool.connection() as conn:
                await conn.execute("UPDATE turns SET sources = %s WHERE id = %s AND jsonb_array_length(sources) = 0",
                                   (json.dumps(docs), turn_id))
        changed += 1
    print(f"{'updated' if apply else 'would update'} {changed} of {len(rows)} turns")
    await c.aclose()
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":  # psycopg async cannot use the Proactor loop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    raise SystemExit(asyncio.run(main(p.parse_args().apply)))
