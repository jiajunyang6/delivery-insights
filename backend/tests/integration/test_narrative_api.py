import asyncio
from datetime import timedelta

import orjson
import pytest
from pydantic import SecretStr
from sqlalchemy import delete, func, select
from tests.fakes import FakeLLMClient
from tests.integration.test_api import DELIVERY
from tests.integration.test_api import api as api
from tests.integration.test_sync import NOW

from insights.analytics.snapshot import digest
from insights.api.schemas import Narrative as NarrativeSchema
from insights.db.models import Narrative, Snapshot
from insights.narrative.evidence import build_evidence_pack
from insights.narrative.llm import LLMUnavailable
from insights.narrative.prompt import PROMPT_VERSION
from insights.narrative.service import check_llm
from insights.narrative.template import build_template
from insights.redis import llm_error_key, narrative_key, narrative_lock_key

pytestmark = pytest.mark.integration


async def prepare(api, *, enabled=False, bad=False):
    client, app, _, ctx = api
    snapshot = (await client.get(DELIVERY)).json()
    pack, _ = build_evidence_pack(snapshot, False)
    valid = build_template(pack)
    if enabled:
        ctx["settings"].aws_bearer_token_bedrock = SecretStr("test")
        app.state.llm = FakeLLMClient(
            [{"narrative": "Bad.", "hypotheses": []}] * 2 if bad else [valid]
        )
    return (
        snapshot,
        valid,
        f"/v1/snapshots/{snapshot['snapshot_id']}/narrative",
    )


@pytest.mark.parametrize("enabled", [False, True])
async def test_narrative_persistence_cache_pack_identity_and_conditional(api, enabled):
    client, app, _, ctx = api
    snapshot, _, url = await prepare(api, enabled=enabled)
    response = await client.get(url)
    assert response.status_code == 200, response.text
    payload = response.json()
    NarrativeSchema.model_validate(payload)
    assert payload["meta"]["generated_by"] == ("llm" if enabled else "template")
    assert response.headers["cache-control"] == "private, max-age=3600"
    conditional = await client.get(url, headers={"If-None-Match": response.headers["etag"]})
    assert conditional.status_code == 304 and not conditional.content
    model_key = ctx["settings"].bedrock_model_id if enabled else "template"
    key = narrative_key(
        snapshot["snapshot_id"],
        PROMPT_VERSION,
        model_key,
        payload["meta"]["pack_hash"],
    )
    assert await ctx["redis"].exists(key)
    assert 0 < await ctx["redis"].ttl(key) <= 86400
    async with ctx["session_factory"]() as session:
        row = (await session.scalars(select(Narrative))).one()
        assert row.pack_hash == payload["meta"]["pack_hash"] and row.model_id == model_key
    await ctx["redis"].delete(key)
    restored = await client.get(url)
    assert restored.content == response.content
    if enabled:
        assert len(app.state.llm.calls) == 1


async def test_validation_failure_is_short_lived_and_not_persisted(api):
    client, app, _, ctx = api
    snapshot, _, url = await prepare(api, enabled=True, bad=True)
    response = await client.get(url)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert (
        payload["meta"]["validation"] == "failed" and payload["meta"]["generated_by"] == "template"
    )
    assert response.headers["cache-control"] == "no-store"
    assert (await client.get(url)).content == response.content
    assert len(app.state.llm.calls) == 2
    key = narrative_key(
        snapshot["snapshot_id"],
        PROMPT_VERSION,
        ctx["settings"].bedrock_model_id,
        payload["meta"]["pack_hash"],
    )
    assert 0 < await ctx["redis"].ttl(key) <= 300
    async with ctx["session_factory"]() as session:
        assert await session.scalar(select(func.count()).select_from(Narrative)) == 0


async def test_unknown_expired_and_invalid_narrative_requests(api):
    client, _, clock, _ = api
    _, _, url = await prepare(api)
    assert (await client.get("/v1/snapshots/s_0000000000000000/narrative")).status_code == 404
    invalid = await client.get("/v1/snapshots/no/narrative")
    assert invalid.status_code == 422
    assert invalid.headers["content-type"].startswith("application/problem+json")
    assert (await client.get(url)).status_code == 200
    clock["now"] += timedelta(days=8)
    assert (await client.get(url)).status_code == 404


async def test_narrative_is_the_english_director_view(api):
    client, _, _, _ = api
    _, _, url = await prepare(api)
    response = await client.get(url)
    ignored = await client.get(url + "?audience=manager&lang=zh")
    assert response.status_code == ignored.status_code == 200
    assert response.content == ignored.content
    payload = response.json()
    assert payload["meta"]["prompt_version"] == PROMPT_VERSION
    NarrativeSchema.model_validate(payload)
    contract = (await client.get("/openapi.json")).json()
    parameters = contract["paths"]["/v1/snapshots/{snapshot_id}/narrative"]["get"]["parameters"]
    assert [p["name"] for p in parameters] == ["snapshot_id"]


async def test_current_prompt_never_reuses_legacy_language_or_prompt_caches(api):
    client, app, clock, ctx = api
    snapshot, _, url = await prepare(api, enabled=True)
    pack, _ = build_evidence_pack(snapshot, False)
    pack_hash = digest(pack)[:16]
    model = ctx["settings"].bedrock_model_id
    for language, version in (("en", "v3"), ("zh", "v3"), ("en", "v4"), ("en", "v6"), ("en", "v7")):
        key = narrative_key(snapshot["snapshot_id"], version, model, pack_hash)
        await ctx["redis"].hset(
            key, mapping={"body": b'{"narrative":"legacy"}', "etag": '"legacy"', "persist": "1"}
        )
        async with ctx["session_factory"]() as session, session.begin():
            session.add(
                Narrative(
                    snapshot_id=snapshot["snapshot_id"],
                    audience="director",
                    lang=language,
                    prompt_version=version,
                    model_id=model,
                    pack_hash=pack_hash,
                    generated_by="llm",
                    payload={"narrative": "legacy"},
                    etag='"legacy"',
                    created_at=clock["now"],
                )
            )
    assert not app.state.llm.calls
    fresh = await client.get(url)
    assert fresh.status_code == 200 and fresh.json()["meta"]["prompt_version"] == PROMPT_VERSION
    assert fresh.json()["narrative"] != "legacy" and len(app.state.llm.calls) == 1
    current_key = narrative_key(snapshot["snapshot_id"], PROMPT_VERSION, model, pack_hash)
    await ctx["redis"].delete(current_key)
    restored = await client.get(url)
    assert restored.content == fresh.content and len(app.state.llm.calls) == 1


async def test_snapshot_deleted_during_generation_returns_404(api):
    client, app, _, ctx = api
    snapshot, valid, url = await prepare(api, enabled=True)

    class DeletingClient(FakeLLMClient):
        async def submit(self, **kwargs):
            async with ctx["session_factory"]() as session, session.begin():
                await session.execute(
                    delete(Snapshot).where(Snapshot.snapshot_id == snapshot["snapshot_id"])
                )
            return await super().submit(**kwargs)

    app.state.llm = DeletingClient([valid])
    response = await client.get(url)
    assert response.status_code == 404, response.text
    assert not await ctx["redis"].exists(narrative_lock_key(snapshot["snapshot_id"]))


async def test_concurrent_generation_is_deduplicated(api):
    client, app, _, ctx = api
    snapshot, valid, url = await prepare(api, enabled=True)
    started, release = asyncio.Event(), asyncio.Event()

    class BlockingClient(FakeLLMClient):
        async def submit(self, **kwargs):
            started.set()
            await release.wait()
            return await super().submit(**kwargs)

    app.state.llm = BlockingClient([valid])
    first = asyncio.create_task(client.get(url))
    await started.wait()
    second = asyncio.create_task(client.get(url))
    await asyncio.sleep(0.05)
    release.set()
    a, b = await asyncio.gather(first, second)
    assert a.status_code == b.status_code == 200
    assert a.content == b.content and len(app.state.llm.calls) == 1
    assert not await ctx["redis"].exists(narrative_lock_key(snapshot["snapshot_id"]))


async def test_busy_and_deadline_fallbacks_release_only_owned_locks(api, monkeypatch):
    client, app, _, ctx = api
    snapshot, valid, url = await prepare(api, enabled=True)
    import insights.narrative.service as service

    monkeypatch.setattr(service, "LOCK_WAIT_SECONDS", 0)
    key = narrative_lock_key(snapshot["snapshot_id"])
    await ctx["redis"].set(key, "other-owner", ex=180)
    busy = await client.get(url)
    assert busy.json()["meta"]["fallback_reason"] == "llm_busy"
    assert busy.headers["cache-control"] == "no-store"
    assert await ctx["redis"].get(key) == b"other-owner"
    assert not app.state.llm.calls
    await ctx["redis"].delete(key)
    monkeypatch.setattr(service, "NARRATIVE_DEADLINE_SECONDS", 0.02)

    class SlowClient(FakeLLMClient):
        async def submit(self, **kwargs):
            await asyncio.sleep(1)
            return await super().submit(**kwargs)

    app.state.llm = SlowClient([valid])
    result = await client.get(url)
    assert result.status_code == 200 and result.json()["meta"]["fallback_reason"] == "llm_error"
    assert result.json()["meta"]["attempts"] == 1
    assert not await ctx["redis"].exists(key)


async def test_repair_uses_remaining_deadline_and_extracts_evidence_once(api, monkeypatch):
    client, app, _, _ = api
    _, valid, url = await prepare(api, enabled=True)
    import insights.narrative.evidence as evidence_module
    import insights.narrative.service as service

    extracted = []
    original = evidence_module.extract_evidence

    def extract(snapshot):
        extracted.append(snapshot["snapshot_id"])
        return original(snapshot)

    monkeypatch.setattr(evidence_module, "extract_evidence", extract)
    monkeypatch.setattr(service, "extract_evidence", extract)
    monkeypatch.setattr(service, "NARRATIVE_DEADLINE_SECONDS", 0.05)

    class RepairTimeoutClient(FakeLLMClient):
        async def submit(self, **kwargs):
            if self.calls:
                await asyncio.sleep(1)
            return await super().submit(**kwargs)

    app.state.llm = RepairTimeoutClient([{"narrative": "Bad.", "hypotheses": []}, valid])
    response = await client.get(url)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["meta"]["attempts"] == 2
    assert response.json()["meta"]["fallback_reason"] == "llm_error"
    assert len(extracted) == 1


async def test_bedrock_failures_are_recorded_for_setup_and_cleared_on_success(api):
    client, app, _, ctx = api
    _, valid, url = await prepare(api, enabled=True)
    app.state.llm = FakeLLMClient([LLMUnavailable("AccessDeniedException")])
    failed = await client.get(url)
    assert failed.json()["meta"]["fallback_reason"] == "llm_error"
    setup = (await client.get("/v1/repos")).json()["setup"]["llm"]
    assert setup["last_error"] == "AccessDeniedException" and setup["last_error_at"]
    model = ctx["settings"].bedrock_model_id
    ctx["settings"].bedrock_model_id = "us.anthropic.other-model"
    assert (await client.get("/v1/repos")).json()["setup"]["llm"]["last_error"] is None
    ctx["settings"].bedrock_model_id = model
    for key in await ctx["redis"].keys("di:narr:*"):
        await ctx["redis"].delete(key)
    app.state.llm = FakeLLMClient([valid])
    assert (await client.get(url)).json()["meta"]["generated_by"] == "llm"
    assert not await ctx["redis"].exists(llm_error_key())


async def test_startup_check_records_and_clears_the_bedrock_status(api):
    _, _, _, ctx = api
    settings, redis = ctx["settings"], ctx["redis"]
    llm = FakeLLMClient([])
    llm.ping_error = LLMUnavailable("UnrecognizedClientException")
    await check_llm(llm, redis, settings, NOW)
    stored = orjson.loads(await redis.get(llm_error_key()))
    assert stored["code"] == "UnrecognizedClientException"
    assert stored["model_id"] == settings.bedrock_model_id
    llm.ping_error = None
    await check_llm(llm, redis, settings, NOW)
    assert not await redis.exists(llm_error_key())
