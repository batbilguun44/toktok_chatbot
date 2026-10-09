import pytest

from app.config import Settings
from app.container import build_container


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None, env="test", llm_provider="fake", database_url=None, redis_url=None,
        embedding_provider="none", langfuse_public_key=None, langfuse_secret_key=None,
        admin_api_key="test-admin", rate_limit_per_minute=1000, staff_session_secret="t" * 40,
        judge_sample_rate=0.0,
    )


@pytest.fixture
async def container(settings):
    c = await build_container(settings)
    yield c
    await c.aclose()


async def collect(events):
    out = []
    async for ev in events:
        out.append(ev)
    return out


def text_of(events) -> str:
    return "".join(e["data"]["delta"] for e in events if e["type"] == "text")
