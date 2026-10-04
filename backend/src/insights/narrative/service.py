"""Narrative orchestration: LLM generation with validation, one repair and template fallback,
payload assembly, and NarrativeService with Redis cache, Postgres persistence and a Redis lock.
"""

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
from insights.db.models import Narrative, Snapshot
from insights.narrative.evidence import build_evidence_pack, extract_evidence
from insights.narrative.hypotheses import LEVELS, STEPS, actions, allowed_ids
from insights.narrative.llm import LLMClient, LLMUnavailable, LLMUsage
from insights.narrative.prompt import PROMPT_VERSION, SYSTEM_PROMPT, TOOL_SPEC, user_message
from insights.narrative.template import build_template
from insights.narrative.validator import citations, validate
from insights.redis import llm_error_key, narrative_key, narrative_lock_key
from insights.snapshots.caching import Reply, cache_ttl, matches_etag
from insights.snapshots.service import SnapshotService, not_found

# One deadline covers the first call and the repair. Waiters poll a little longer than that,
# and the lock TTL set in get() (deadline + 30 s) outlives both, so a crashed holder's lock
# expires. UNLOCK deletes only a lock still holding this request's token.
NARRATIVE_DEADLINE_SECONDS = 150
LOCK_WAIT_SECONDS = NARRATIVE_DEADLINE_SECONDS + 10
UNLOCK = (
    "if redis.call('get',KEYS[1]) == ARGV[1] then "
    "return redis.call('del',KEYS[1]) else return 0 end"
)
logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class NarrativeResult:
    payload: dict[str, Any]
    persist: bool
    # Bedrock error code when a call failed; llm_reached means Bedrock returned an answer.
    llm_error: str | None = None
    llm_reached: bool = False


def assemble(
    output: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    pack: dict[str, Any],
    candidates: list[dict[str, Any]],
    meta: dict[str, Any],
    *,
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Combine validated LLM or template output with the scored candidates into the payload.

    A downgrade lowers the level and caps confidence inside the new band. The response lists
    only evidence that is cited or in a hypothesis's allowed set, with example URLs kept.
    """
    by_id = {c["id"]: c for c in candidates}
    hypotheses: list[dict[str, Any]] = []
    evidence_ids = citations(output["narrative"])
    for text in output["hypotheses"]:
        candidate = deepcopy(by_id[text["id"]])
        confidence, final_level = candidate["confidence"], candidate["confidence_level"]
        if downgrade := text.get("downgrade"):
            final_level = downgrade["level"]
            confidence = min(confidence, LEVELS[final_level].downgrade_cap)
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
        evidence_ids.update(allowed_ids(candidate))
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
                "confidence": LEVELS["low"].minimum,
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
    if evidence is None:
        evidence = extract_evidence(snapshot)
    evidence = sorted(
        (e for e in evidence if e["id"] in evidence_ids),
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
    prepared: tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]] | None = None,
    fallback_reason: str | None = None,
) -> NarrativeResult:
    """Generate a narrative payload, preferring a validated LLM answer over the template.

    persist is True for a validated LLM answer, or for the template when the LLM is
    disabled; fallbacks after LLM errors, validation failure or a busy lock are served but
    not stored in Postgres, so a later request can try the LLM again after any short Redis
    cache expires. Both LLM attempts share one deadline; repair gets only the remaining time.
    """
    started = perf_counter()
    deadline = monotonic() + NARRATIVE_DEADLINE_SECONDS
    if prepared is None:
        evidence = extract_evidence(snapshot)
        pack, candidates = build_evidence_pack(snapshot, audience, ci_complete, evidence=evidence)
    else:
        pack, candidates, evidence = prepared
    meta: dict[str, Any] = {
        "generated_by": "template",
        "model": "template",
        "prompt_version": PROMPT_VERSION,
        "validation": "not_run",
        "attempts": 0,
        "fallback_reason": fallback_reason or "llm_disabled",
        "violations": [],
        "confidence_method": "deterministic-v1",
        "pack_hash": digest(pack)[:16],
        "generated_at": iso(now),
    }
    output = build_template(pack, snapshot)
    persist = llm is None and fallback_reason is None
    llm_error = "client_unavailable" if fallback_reason == "llm_error" else None
    llm_reached = False
    usage = LLMUsage()
    if llm is not None:
        messages = [user_message(pack)]
        for attempt in (1, 2):
            usage.attempts += 1
            meta["attempts"] = usage.attempts
            try:
                reply = await asyncio.wait_for(
                    llm.submit(system=SYSTEM_PROMPT, messages=messages, tool_spec=TOOL_SPEC),
                    timeout=max(0, deadline - monotonic()),
                )
            except TimeoutError:
                meta.update(fallback_reason="llm_error", validation="not_run")
                llm_error = "timeout"
                break
            except LLMUnavailable as exc:
                meta.update(fallback_reason="llm_error", validation="not_run")
                logger.warning("llm_unavailable", reason=exc.reason)
                llm_error = exc.reason
                break
            llm_reached = True
            usage.record(reply)
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
            # One repair bounds latency and tokens; a second invalid answer falls back to the
            # deterministic template instead of another attempt.
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
                    "\nCall submit_narrative again with a corrected answer. "
                    "Keep everything that was valid."
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
            # Preserve the rejected answer and its tool-use ID so repair addresses these
            # exact violations rather than producing an unrelated answer from the pack alone.
            messages += [reply.assistant_message, {"role": "user", "content": [content]}]
    payload = assemble(output, snapshot, pack, candidates, meta, evidence=evidence)
    logger.info(
        "narrative_generated",
        snapshot_id=snapshot["snapshot_id"],
        audience=audience,
        generated_by=meta["generated_by"],
        attempts=meta["attempts"],
        validation=meta["validation"],
        fallback_reason=meta["fallback_reason"],
        violations=meta["violations"],
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        duration_ms=round((perf_counter() - started) * 1000, 2),
    )
    return NarrativeResult(payload, persist, llm_error, llm_reached)


def narrative_reply(body: bytes, tag: str, conditional: str | None, persist: bool) -> Reply:
    """Build a 200/304 reply; persisted output is private-cacheable, fallbacks are no-store."""
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
        """Bind snapshot/cache access and an optional LLM client for narrative orchestration."""
        self.snapshots, self.llm = snapshots, llm

    async def record_llm_status(self, result: NarrativeResult) -> None:
        """Remember the latest Bedrock failure for the setup hint; clear it after a success."""
        redis = self.snapshots.redis
        if result.llm_error:
            value = orjson.dumps({"code": result.llm_error, "at": iso(self.snapshots.now)})
            await self.snapshots.cache(redis.set(llm_error_key(), value, ex=7 * 86400))
        elif result.llm_reached:
            await self.snapshots.cache(redis.delete(llm_error_key()))

    async def cached(self, key: str, conditional: str | None) -> Reply | None:
        """Return a complete cached conditional reply, or None on a miss/cache failure."""
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
        """Best-effort cache of payload, ETag and persistence flag, skipping nonpositive TTLs."""
        if ttl > 0:
            pipe = self.snapshots.redis.pipeline()
            pipe.hset(key, mapping={"body": body, "etag": tag, "persist": "1" if persist else "0"})
            pipe.expire(key, ttl)
            await self.snapshots.cache(pipe.execute())

    async def get(self, sid: str, audience: str, conditional: str | None) -> Reply:
        """Serve a narrative from Redis, then Postgres, then fresh generation.

        With the LLM enabled, one request per snapshot, audience and lang holds the lock;
        others poll the cache and, after LOCK_WAIT_SECONDS, return an unstored template
        (llm_busy). On a concurrent insert the stored row wins, so every client gets the
        same body and ETag. Redis failures fail open; generation proceeds without the lock.
        """
        lang = "en"
        snapshot = orjson.loads((await self.snapshots.by_id(sid, None)).body)
        settings, redis = self.snapshots.settings, self.snapshots.redis
        evidence = extract_evidence(snapshot)
        pack, candidates = build_evidence_pack(
            snapshot, audience, settings.ci_complete, evidence=evidence
        )
        prepared = (pack, candidates, evidence)
        pack_hash = digest(pack)[:16]
        model_key = settings.bedrock_model_id if settings.llm_enabled else "template"
        # A new prompt version, model or pack content gives a new key, so stale wording is
        # never served; the same identity keys the Postgres row.
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
        # The lock is broader than the cache identity: even different model/prompt versions
        # serialize generation for this snapshot and audience while Redis is available.
        lock, token, locked = narrative_lock_key(sid, audience, lang), uuid4().hex, False
        if settings.llm_enabled:
            try:
                acquired = await redis.set(
                    lock, token, nx=True, ex=int(NARRATIVE_DEADLINE_SECONDS + 30)
                )
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
                result = await generate(
                    snapshot,
                    audience=audience,
                    llm=None,
                    ci_complete=settings.ci_complete,
                    now=self.snapshots.now,
                    prepared=prepared,
                    fallback_reason="llm_busy",
                )
                body = canonical(result.payload)
                return narrative_reply(body, etag(body), conditional, False)
        try:
            result = await generate(
                snapshot,
                audience=audience,
                llm=self.llm if settings.llm_enabled else None,
                ci_complete=settings.ci_complete,
                now=self.snapshots.now,
                prepared=prepared,
                fallback_reason="llm_error" if settings.llm_enabled and self.llm is None else None,
            )
            await self.record_llm_status(result)
            body = canonical(result.payload)
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
            # Unstored fallbacks expire within five minutes so the LLM is retried soon.
            await self.put_cache(
                key, body, tag, result.persist, ttl if result.persist else min(ttl, 300)
            )
            return narrative_reply(body, tag, conditional, result.persist)
        finally:
            if locked:
                with suppress(RedisError, OSError, TimeoutError):
                    await cast(Awaitable[Any], redis.eval(UNLOCK, 1, lock, token))
