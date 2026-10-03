import asyncio
from datetime import timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, func, select
from tests.integration.test_api import DELIVERY
from tests.integration.test_api import api as api

from insights.analytics.snapshot import digest
from insights.api.schemas import Narrative as NarrativeSchema
from insights.db.models import Narrative, Snapshot
from insights.narrative.evidence import build_evidence_pack
from insights.narrative.llm import FakeLLMClient
from insights.narrative.prompt import PROMPT_VERSION
from insights.narrative.template import build_template
from insights.redis import narrative_key, narrative_lock_key

pytestmark = pytest.mark.integration


async def prepare(api, *, enabled=False, bad=False):
    client, app, _, ctx = api
    snapshot = (await client.get(DELIVERY)).json()
    pack, _ = build_evidence_pack(snapshot, "director", False)
    valid = build_template(pack, snapshot)
    if enabled:
        ctx["settings"].aws_bearer_token_bedrock = SecretStr("test")
        app.state.llm = FakeLLMClient(
            [{"narrative": "Bad.", "hypotheses": []}] * 2 if bad else [valid]
        )
    return (
        snapshot,
        valid,
        f"/v1/snapshots/{snapshot['snapshot_id']}/narrative?audience=director&lang=en",
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
        "director",
        "en",
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
        "director",
        "en",
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
    assert (await client.get("/v1/snapshots/no/narrative")).status_code == 422
    for query in ("audience=administrator", "lang=xx", "lang=zh"):
        response = await client.get(url.split("?")[0] + "?" + query)
        assert response.status_code == 422 and response.headers["content-type"].startswith(
            "application/problem+json"
        )
        assert query.split("=")[1] not in response.text
        assert response.json()["errors"][0]["param"] == query.split("=")[0]
    assert (await client.get(url)).status_code == 200
    clock["now"] += timedelta(days=8)
    assert (await client.get(url)).status_code == 404


@pytest.mark.parametrize("audience", ["director", "manager"])
async def test_only_english_default_and_explicit_language_match(api, audience):
    client, _, _, _ = api
    snapshot, _, _ = await prepare(api)
    url = f"/v1/snapshots/{snapshot['snapshot_id']}/narrative?audience={audience}"
    default = await client.get(url)
    explicit = await client.get(url + "&lang=en")
    assert default.status_code == explicit.status_code == 200
    assert default.content == explicit.content
    assert default.headers["etag"] == explicit.headers["etag"]
    payload = default.json()
    assert payload["lang"] == "en" and payload["meta"]["prompt_version"] == "v6"
    NarrativeSchema.model_validate(payload)
    contract = (await client.get("/openapi.json")).json()
    parameters = contract["paths"]["/v1/snapshots/{snapshot_id}/narrative"]["get"]["parameters"]
    language = next(p["schema"] for p in parameters if p["name"] == "lang")
    assert language.get("const") == "en" or language.get("enum") == ["en"]


async def test_v6_never_reuses_legacy_language_or_prompt_caches(api):
    client, app, clock, ctx = api
    snapshot, _, url = await prepare(api, enabled=True)
    pack, _ = build_evidence_pack(snapshot, "director", False)
    pack_hash = digest(pack)[:16]
    model = ctx["settings"].bedrock_model_id
    for language, version in (("en", "v3"), ("zh", "v3"), ("en", "v4")):
        key = narrative_key(
            snapshot["snapshot_id"], "director", language, version, model, pack_hash
        )
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
    rejected = await client.get(url.replace("lang=en", "lang=zh"))
    assert rejected.status_code == 422 and not app.state.llm.calls
    fresh = await client.get(url)
    assert fresh.status_code == 200 and fresh.json()["meta"]["prompt_version"] == "v6"
    assert fresh.json()["narrative"] != "legacy" and len(app.state.llm.calls) == 1
    current_key = narrative_key(snapshot["snapshot_id"], "director", "en", "v6", model, pack_hash)
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
    assert not await ctx["redis"].exists(
        narrative_lock_key(snapshot["snapshot_id"], "director", "en")
    )


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
    assert not await ctx["redis"].exists(
        narrative_lock_key(snapshot["snapshot_id"], "director", "en")
    )


async def test_busy_and_deadline_fallbacks_release_only_owned_locks(api, monkeypatch):
    client, app, _, ctx = api
    snapshot, valid, url = await prepare(api, enabled=True)
    import insights.narrative.service as service

    monkeypatch.setattr(service, "LOCK_WAIT_SECONDS", 0)
    key = narrative_lock_key(snapshot["snapshot_id"], "director", "en")
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
