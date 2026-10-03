from collections.abc import Mapping
from typing import Any

from insights.narrative.validator import chain_ids

SUBJECTS = {
    "H_ci_bottleneck": ("Slow or congested CI", "CI 排队或运行变慢"),
    "H_pr_size_growth": ("Larger pull requests", "PR 变大"),
    "H_quality_tradeoff": ("Lighter review in exchange for speed", "放松 review 换来的提速"),
}
FINDINGS = {
    "review_queue_growth": (
        "review demand exceeding first reviews",
        "review 新进需求超过首次 review",
    ),
    "review_concentration": ("reviews concentrated on a few people", "review 集中在少数人"),
    "merge_blocked": ("approved PRs waiting to merge", "批准后迟迟不合并"),
    "ci_wait": ("waiting on CI", "等待 CI"),
    "rework_high": ("rework after review", "review 后的返工"),
    "waste_high": ("work that never shipped", "没有交付的工作"),
    "quality_guardrail": ("a quality warning", "质量护栏告警"),
    "external_contributor_wait": (
        "slow first reviews for external contributors",
        "外部贡献者等待 review",
    ),
}


def percent(value: float) -> str:
    return f"{value * 100:.1f}%" if abs(value * 100) < 10 else f"{value * 100:.0f}%"


def phrase(candidate: Mapping[str, Any], lang: str) -> str:
    zh = lang == "zh"
    if candidate["id"] == "H_review_capacity":
        location = candidate["location"]
        subject = (
            ("review 人手不足" if not location else f"{location} 的 review 人手不足")
            if zh
            else (
                "Limited review capacity"
                if not location
                else f"Limited review capacity in {location}"
            )
        )
    else:
        subject = SUBJECTS[candidate["id"]][int(zh)]
    level = candidate["level"]
    if zh:
        return (
            f"{subject}很可能是主要原因"
            if level == "high"
            else f"{subject}可能是主要原因"
            if level == "medium"
            else f"有初步迹象表明，{subject}是主要原因"
        )
    return (
        f"{subject} is likely the main cause"
        if level == "high"
        else f"{subject} may be the main cause"
        if level == "medium"
        else f"There are early signs that {subject[0].lower() + subject[1:]} is the main cause"
    )


def build_template(pack: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict[str, Any]:
    lang, audience = pack["lang"], pack["audience"]
    zh = lang == "zh"
    evidence = {e["id"]: e for e in pack["evidence"]}
    candidates = pack["hypotheses"]

    def hour(value: float) -> str:
        return f"{value:.1f} 小时" if zh else f"{value:.1f} h"

    def sentence(text: str, ids: list[str]) -> str:
        return f"{text} {''.join(f'[{i}]' for i in ids[:3])}" + ("。" if zh else ".")

    cycle = evidence.get("E1")
    if cycle is None:
        count = evidence["E3"]["value"]
        s1 = sentence(
            f"本期只合并了 {count} 个 PR，样本太少，无法给出可靠的周期统计"
            if zh
            else f"Only {count} PRs were merged, too few for reliable cycle-time statistics",
            ["E3"],
        )
    elif cycle["previous"] is None:
        s1 = sentence(
            f"交付周期中位数为 {hour(cycle['value'])}，没有可比较的上一周期"
            if zh
            else (
                f"Median cycle time was {hour(cycle['value'])}; "
                "there is no previous period to compare"
            ),
            ["E1"],
        )
    elif cycle["significant"] is True and cycle["change_rel"] is not None:
        delta = percent(abs(cycle["change_rel"]))
        current, previous = hour(cycle["value"]), hour(cycle["previous"])
        if zh:
            verb = "上升" if cycle["change_rel"] > 0 else "下降"
            text = f"交付周期中位数{verb}了 {delta}，从 {previous} 变为 {current}"
        else:
            verb = "rose" if cycle["change_rel"] > 0 else "fell"
            text = f"Median cycle time {verb} {delta} to {current} from {previous}"
        s1 = sentence(text, ["E1"])
    else:
        s1 = sentence(
            f"交付周期中位数为 {hour(cycle['value'])}，与上一周期相比没有显著变化"
            if zh
            else (
                f"Median cycle time was {hour(cycle['value'])}, "
                "with no significant change from the previous period"
            ),
            ["E1"],
        )
    parts = {"S1": s1}
    if audience == "manager" and "E6" in evidence:
        share = percent(evidence["E6"]["value"])
        parts["S2"] = sentence(
            f"PR 有 {share} 的交付周期在等待 reviewer、CI 或合并"
            if zh
            else f"PRs spent {share} of their cycle time waiting on reviewers, CI or merge",
            ["E6"],
        )
    if pack["top_bottlenecks"] and "E71" in evidence:
        finding = pack["top_bottlenecks"][0]
        location = finding["location"]
        if finding["type"] == "review_capacity":
            name = (
                f"{location} 的首次 review 等待" if zh else f"the first-review wait in {location}"
            )
        else:
            name = FINDINGS[finding["type"]][int(zh)]
        share = percent(evidence["E71"]["value"])
        parts["S3"] = sentence(
            f"耗时最多的环节是{name}，约占 PR 时间的 {share}"
            if zh
            else f"The largest time sink is {name}, about {share} of PR time",
            ["E71"],
        )
    if audience == "manager" and evidence.get("E25", {}).get("value", 0) > 0:
        count, critical = evidence["E25"]["value"], evidence["E25"]["extra"]["critical"]
        parts["S4"] = sentence(
            f"有 {count} 个开着的 PR 等待时间超过往常，其中 {critical} 个严重超时"
            if zh
            else f"{count} open PRs are waiting longer than usual, {critical} of them critically",
            ["E25"],
        )
    if candidates:
        parts["S5"] = sentence(phrase(candidates[0], lang), chain_ids(candidates[0]))
    else:
        parts["S5"] = sentence(
            "本期信号不足，无法给出有证据支持的根因"
            if zh
            else ("The signals are insufficient to support a specific root cause this period"),
            ["E1" if cycle else "E3"],
        )
    if snapshot["guardrail"]["verdict"] != "ok" and "E10" in evidence:
        share = percent(evidence["E10"]["value"])
        parts["S6"] = sentence(
            f"revert 率为 {share}，继续提速前先检查 review 是否充分"
            if zh
            else f"The revert rate is {share}, so check review depth before pushing for more speed",
            ["E10"],
        )
    order = (
        ("S1", "S5", "S3", "S6") if audience == "director" else ("S1", "S2", "S3", "S4", "S5", "S6")
    )
    hypotheses = []
    for c in candidates:
        text = phrase(c, lang) + " " + "".join(f"[{i}]" for i in chain_ids(c)[:3])
        if c["counter_evidence"]:
            text += "，但也存在反证 " if zh else ", although there is counter-evidence "
            text += f"[{c['counter_evidence'][0]}]"
        hypotheses.append({"id": c["id"], "statement": text + ("。" if zh else ".")})
    return {"narrative": " ".join(parts[k] for k in order if k in parts), "hypotheses": hypotheses}
