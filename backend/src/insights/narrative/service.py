import asyncio
from collections.abc import Awaitable, Mapping
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from time import monotonic, perf_counter
from typing import Any, cast
from uuid import uuid4

import orjson
import structlog
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from insights.analytics.snapshot import canonical, digest, etag, iso
from insights.api.caching import Reply, cache_ttl, matches_etag
from insights.db.models import Narrative, Snapshot
from insights.narrative.evidence import build_evidence_pack, extract_evidence
from insights.narrative.hypotheses import actions
from insights.narrative.llm import LLMClient, LLMReply, LLMUnavailable
from insights.narrative.prompt import PROMPT_VERSION, SYSTEM_PROMPT, TOOL_SPEC, user_message
from insights.narrative.template import build_template
from insights.narrative.validator import STEPS, citations, validate
from insights.redis import narrative_key, narrative_lock_key
from insights.snapshots.service import SnapshotService, not_found

NARRATIVE_DEADLINE_SECONDS = 150
LOCK_WAIT_SECONDS = 160
UNLOCK = (
    "if redis.call('get',KEYS[1]) == ARGV[1] then return red"
    "is.call('del',KEYS[1]) else return 0 end"
)
logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class NarrativeResult:
    payload: dict[str, Any]
    persist: bool


def assemble(
    output: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    pack: dict[str, Any],
    candidates: list[dict[str, Any]],
    meta: dict[str, Any],
) -> dict[str, Any]:
    by_id = {c["id"]: c for c in candidates}
    hypotheses: list[dict[str, Any]] = []
    evidence_ids = citations(output["narrative"])
    for text in output["hypotheses"]:
        candidate = deepcopy(by_id[text["id"]])
        confidence, final_level = candidate["confidence"], candidate["confidence_level"]
        if downgrade := text.get("downgrade"):
            final_level = downgrade["level"]
            confidence = min(confidence, {"medium": 0.74, "low": 0.5}[final_level])
            candidate["confidence_basis"]["llm_downgrade"] = {
                "from": candidate["confidence_level"],
                "to": final_level,
                "reason": downgrade["reason"],
            }
            evidence_ids.update(citations(downgrade["reason"]))
        chain = [
            {"step": step, "evidence": candidate["chain"][step]}
            for step in STEPS
            if candidate["chain"][step]
        ]
        action, verify = actions(candidate["id"], candidate["location"])
        hypotheses.append(
            {
                "id": candidate["id"],
                "source": "library",
                "title": candidate["title"],
                "location": candidate["location"],
                "statement": text["statement"],
                "confidence": confidence,
                "confidence_level": final_level,
                "confidence_basis": candidate["confidence_basis"],
                "evidence_chain": chain,
                "counter_evidence": candidate["counter_evidence"],
                "alternatives_ruled_out": candidate["alternatives_ruled_out"],
                "alternatives_open": candidate["alternatives_open"],
                "action": action,
                "verify_next": verify,
            }
        )
        evidence_ids.update(citations(text["statement"]))
        evidence_ids.update(candidate["counter_evidence"])
        evidence_ids.update(i for step in chain for i in step["evidence"])
        evidence_ids.update(i for a in candidate["alternatives_ruled_out"] for i in a["evidence"])
    if outside := output.get("llm_hypothesis"):
        basis = dict.fromkeys(candidates[0]["confidence_basis"])
        basis["cap_reason"] = "outside_library"
        hypotheses.append(
            {
                "id": "H_llm",
                "source": "llm",
                "title": "Other explanation",
                "location": None,
                "statement": outside["statement"],
                "confidence": 0.35,
                "confidence_level": "low",
                "confidence_basis": basis,
                "evidence_chain": [{"step": "cited", "evidence": outside["evidence_ids"]}],
                "counter_evidence": [],
                "alternatives_ruled_out": [],
                "alternatives_open": [],
                "action": None,
                "verify_next": None,
            }
        )
        evidence_ids.update(outside["evidence_ids"])
        evidence_ids.update(citations(outside["statement"]))
    hypotheses.sort(key=lambda h: (-h["confidence"], h["source"] != "library", h["id"]))
    evidence = sorted(
        (e for e in extract_evidence(snapshot) if e["id"] in evidence_ids),
        key=lambda e: int(e["id"][1:]),
    )
    return {
        "snapshot_id": snapshot["snapshot_id"],
        "audience": pack["audience"],
        "lang": pack["lang"],
        "narrative": output["narrative"],
        "abstained": not candidates,
        "abstain_reason": pack["abstain_reason"],
        "hypotheses": hypotheses,
        "evidence": evidence,
        "links": {"snapshot": f"/v1/snapshots/{snapshot['snapshot_id']}"},
        "meta": meta,
    }


async def generate(
    snapshot: Mapping[str, Any],
    *,
    audience: str,
    llm: LLMClient | None,
    ci_complete: bool,
    now: datetime,
) -> NarrativeResult:
    started = perf_counter()
    pack, candidates = build_evidence_pack(snapshot, audience, ci_complete)
    meta: dict[str, Any] = {
        "generated_by": "template",
        "model": "template",
        "prompt_version": PROMPT_VERSION,
        "validation": "not_run",
        "attempts": 0,
        "fallback_reason": "llm_disabled",
        "violations": [],
        "confidence_method": "deterministic-v1",
        "pack_hash": digest(pack)[:16],
        "generated_at": iso(now),
    }
    output = build_template(pack, snapshot)
    persist = llm is None
    input_tokens, output_tokens = 0, 0
    if llm is not None:
        messages = [user_message(pack)]
        for attempt in (1, 2):
            meta["attempts"] = attempt
            try:
                reply = await llm.submit(
                    system=SYSTEM_PROMPT, messages=messages, tool_spec=TOOL_SPEC
                )
            except LLMUnavailable as exc:
                meta.update(fallback_reason="llm_error", validation="not_run")
                logger.warning("llm_unavailable", reason=exc.reason)
                break
            input_tokens += reply.input_tokens
            output_tokens += reply.output_tokens
            violations = validate(reply.tool_input, pack, snapshot, audience=audience)
            if not violations and reply.tool_input is not None:
                output = reply.tool_input
                meta.update(
                    generated_by="llm",
                    model=llm.model_id,
                    validation="passed",
                    fallback_reason=None,
                    violations=[],
                )
                persist = True
                break
            if attempt == 2:
                meta.update(
                    validation="failed",
                    fallback_reason="validation_failed",
                    violations=list(dict.fromkeys(v.code for v in violations)),
                )
                break
            feedback = (
                "Your previous answer failed validation:\n"
                + "\n".join(f"- {v.code}: {v.message}" for v in violations)
                + (
                    "\nCall submit_narrative again with a corrected answer. K"
                    "eep everything that was valid."
                )
            )
            content = (
                {
                    "toolResult": {
                        "toolUseId": reply.tool_use_id,
                        "content": [{"text": feedback}],
                        "status": "error",
                    }
                }
                if reply.tool_use_id
                else {"text": feedback}
            )
            messages += [reply.assistant_message, {"role": "user", "content": [content]}]
    payload = assemble(output, snapshot, pack, candidates, meta)
    logger.info(
        "narrative_generated",
        snapshot_id=snapshot["snapshot_id"],
        audience=audience,
        lang="en",
        generated_by=meta["generated_by"],
        attempts=meta["attempts"],
        validation=meta["validation"],
        fallback_reason=meta["fallback_reason"],
        violations=meta["violations"],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        duration_ms=round((perf_counter() - started) * 1000, 2),
    )
    return NarrativeResult(payload, persist)


class CountingClient:
    def __init__(self, client: LLMClient) -> None:
        self.client = client
        self.model_id = client.model_id
        self.attempts = 0

    async def submit(
        self, *, system: str, messages: list[dict[str, Any]], tool_spec: dict[str, Any]
    ) -> LLMReply:
        self.attempts += 1
        return await self.client.submit(system=system, messages=messages, tool_spec=tool_spec)


def narrative_reply(body: bytes, tag: str, conditional: str | None, persist: bool) -> Reply:
    matched = matches_etag(conditional, tag)
    return Reply(
        b"" if matched else body,
        304 if matched else 200,
        {
            "ETag": tag,
            "Cache-Control": "private, max-age=3600" if persist else "no-store",
        },
    )


class NarrativeService:
    def __init__(self, snapshots: SnapshotService, llm: LLMClient | None) -> None:
        self.snapshots, self.llm = snapshots, llm

    async def cached(self, key: str, conditional: str | None) -> Reply | None:
        values = await self.snapshots.cache(cast(Awaitable[Any], self.snapshots.redis.hgetall(key)))
        if values and values.get(b"body") and values.get(b"etag"):
            return narrative_reply(
                values[b"body"],
                values[b"etag"].decode(),
                conditional,
                values.get(b"persist") == b"1",
            )
        return None

    async def put_cache(self, key: str, body: bytes, tag: str, persist: bool, ttl: int) -> None:
        if ttl > 0:
            pipe = self.snapshots.redis.pipeline()
            pipe.hset(key, mapping={"body": body, "etag": tag, "persist": "1" if persist else "0"})
            pipe.expire(key, ttl)
            await self.snapshots.cache(pipe.execute())

    async def fallback(
        self, snapshot: dict[str, Any], audience: str, reason: str, attempts: int = 0
    ) -> NarrativeResult:
        result = await generate(
            snapshot,
            audience=audience,
            llm=None,
            ci_complete=self.snapshots.settings.ci_complete,
            now=self.snapshots.now,
        )
        result.payload["meta"].update(fallback_reason=reason, attempts=attempts)
        return NarrativeResult(result.payload, False)

    async def get(self, sid: str, audience: str, conditional: str | None) -> Reply:
        lang = "en"
        snapshot = orjson.loads((await self.snapshots.by_id(sid, None)).body)
        settings, redis = self.snapshots.settings, self.snapshots.redis
        pack, _ = build_evidence_pack(snapshot, audience, settings.ci_complete)
        pack_hash = digest(pack)[:16]
        model_key = settings.bedrock_model_id if settings.llm_enabled else "template"
        key = narrative_key(sid, audience, lang, PROMPT_VERSION, model_key, pack_hash)
        if cached := await self.cached(key, conditional):
            return cached
        identity = {
            "snapshot_id": sid,
            "audience": audience,
            "lang": lang,
            "prompt_version": PROMPT_VERSION,
            "model_id": model_key,
            "pack_hash": pack_hash,
        }
        async with self.snapshots.sessions() as session:
            created = await session.scalar(
                select(Snapshot.created_at).where(Snapshot.snapshot_id == sid)
            )
            existing = await session.scalar(select(Narrative).filter_by(**identity))
        if created is None:
            raise not_found()
        ttl = cache_ttl(created, self.snapshots.now)
        if existing:
            body = canonical(existing.payload)
            await self.put_cache(key, body, existing.etag, True, ttl)
            return narrative_reply(body, existing.etag, conditional, True)
        lock, token, locked = narrative_lock_key(sid, audience, lang), uuid4().hex, False
        if settings.llm_enabled:
            try:
                acquired = await redis.set(lock, token, nx=True, ex=180)
            except (RedisError, OSError, TimeoutError):
                acquired = True  # Cache failure is a documented fail-open condition.
                logger.warning("narrative_lock_unavailable")
            else:
                locked = bool(acquired)
            if not acquired:
                deadline = monotonic() + LOCK_WAIT_SECONDS
                while monotonic() < deadline:
                    await asyncio.sleep(1)
                    if cached := await self.cached(key, conditional):
                        return cached
                result = await self.fallback(snapshot, audience, "llm_busy")
                body = canonical(result.payload)
                return narrative_reply(body, etag(body), conditional, False)
        try:
            counted = CountingClient(self.llm) if settings.llm_enabled and self.llm else None
            try:
                result = await asyncio.wait_for(
                    generate(
                        snapshot,
                        audience=audience,
                        llm=counted,
                        ci_complete=settings.ci_complete,
                        now=self.snapshots.now,
                    ),
                    timeout=NARRATIVE_DEADLINE_SECONDS,
                )
                if settings.llm_enabled and counted is None:
                    result = await self.fallback(snapshot, audience, "llm_error")
            except TimeoutError:
                result = await self.fallback(
                    snapshot, audience, "llm_error", counted.attempts if counted else 0
                )
            body, tag = canonical(result.payload), ""
            tag = etag(body)
            if result.persist:
                try:
                    async with self.snapshots.sessions() as session, session.begin():
                        inserted = await session.scalar(
                            insert(Narrative)
                            .values(
                                **identity,
                                generated_by=result.payload["meta"]["generated_by"],
                                payload=result.payload,
                                etag=tag,
                                created_at=self.snapshots.now,
                            )
                            .on_conflict_do_nothing()
                            .returning(Narrative.id)
                        )
                        if inserted is None:
                            winner = (
                                await session.scalars(select(Narrative).filter_by(**identity))
                            ).one()
                            body, tag = canonical(winner.payload), winner.etag
                except IntegrityError as exc:
                    if getattr(exc.orig, "sqlstate", None) == "23503":
                        raise not_found() from None
                    raise
            await self.put_cache(
                key, body, tag, result.persist, ttl if result.persist else min(ttl, 300)
            )
            return narrative_reply(body, tag, conditional, result.persist)
        finally:
            if locked:
                with suppress(RedisError, OSError, TimeoutError):
                    await cast(Awaitable[Any], redis.eval(UNLOCK, 1, lock, token))
