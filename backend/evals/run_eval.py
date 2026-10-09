"""Run the regression set against the configured stack.

    python -m evals.run_eval                 # uses .env (LLM_PROVIDER=anthropic needs an API key)
    python -m evals.run_eval --provider fake # offline smoke run (expect low scores)
    python -m evals.run_eval --only cust-    # subset by id prefix

Every case uses fresh in-memory stores and a fresh mock gateway, so runs are
independent. Prints a per-case table and writes evals/last_run.json.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

import yaml

from app.config import Settings
from app.container import build_container

HERE = Path(__file__).parent


async def run_case(case: dict, settings: Settings) -> dict:
    c = await build_container(settings)
    try:
        user = await c.services.gateway.resolve_user(case["token"])
        session_id, events, intents, t0 = None, [], [], time.time()
        for turn in case["turns"]:
            events = []
            async for ev in c.chat.stream_turn(user, session_id, turn):
                events.append(ev)
                if ev["type"] == "meta" and "session_id" in ev["data"]:
                    session_id = ev["data"]["session_id"]
                if ev["type"] == "meta" and "intent" in ev["data"]:
                    intents.append(ev["data"]["intent"])
            await asyncio.sleep(0)
        await c.services.drain()
        repo = c.services.repo
        all_tools = [t["name"] for log in repo.turns for t in log.tool_calls]
        answer = "".join(e["data"]["delta"] for e in events if e["type"] == "text")
        escalated = bool(repo.escalations)
    finally:
        await c.aclose()

    checks: dict[str, bool] = {}
    if "intent" in case:
        checks["intent"] = bool(intents) and intents[-1] in case["intent"]
    for t in case.get("tools", []):
        checks[f"tool:{t}"] = t in all_tools
    for t in case.get("no_tools", []):
        checks[f"no_tool:{t}"] = t not in all_tools
    if "escalate" in case:
        checks["escalate"] = escalated == case["escalate"]
    if "include_any" in case:
        checks["include_any"] = any(s in answer for s in case["include_any"])
    for s in case.get("exclude", []):
        checks[f"exclude:{s}"] = s not in answer
    return {"id": case["id"], "passed": all(checks.values()), "checks": checks, "intent": intents[-1:] or None,
            "tools": all_tools, "answer": answer, "seconds": round(time.time() - t0, 1)}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["anthropic", "fake"])
    ap.add_argument("--only", default="")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--min-pass-rate", type=float, default=0.0,
                    help="exit non-zero if the share of passing cases is below this (CI gate)")
    args = ap.parse_args()

    overrides = {"database_url": None, "redis_url": None, "qdrant_url": None, "langfuse_public_key": None,
                 "rate_limit_per_minute": 10_000, "judge_sample_rate": 0.0}
    if args.provider:
        overrides["llm_provider"] = args.provider
    settings = Settings(**overrides)
    cases = [c for c in yaml.safe_load((HERE / "cases.yaml").read_text()) if c["id"].startswith(args.only)]

    sem = asyncio.Semaphore(args.concurrency)

    async def guarded(case):
        async with sem:
            return await run_case(case, settings)

    results = await asyncio.gather(*(guarded(c) for c in cases))
    for r in results:
        failed = [k for k, v in r["checks"].items() if not v]
        print(f"{'PASS' if r['passed'] else 'FAIL'}  {r['id']:<34} {r['seconds']:>5}s  {', '.join(failed)}")
    n_checks = sum(len(r["checks"]) for r in results)
    ok_checks = sum(sum(r["checks"].values()) for r in results)
    print(f"\ncases {sum(r['passed'] for r in results)}/{len(results)}   checks {ok_checks}/{n_checks}   "
          f"provider={settings.llm_provider} model={settings.agent_model} effort={settings.agent_effort}")
    (HERE / "last_run.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    rate = sum(r["passed"] for r in results) / max(len(results), 1)
    if rate < args.min_pass_rate:
        print(f"FAILED: pass rate {rate:.0%} < required {args.min_pass_rate:.0%}")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
