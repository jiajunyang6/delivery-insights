from copy import deepcopy
from pathlib import Path

import orjson


def golden():
    return orjson.loads((Path(__file__).parent / "golden/snapshot_seed42.json").read_bytes())


def entry(identifier, value, previous=None, *, unit="hours", n=100, significant=True, **extra):
    delta = value - previous if previous is not None else None
    return {
        "id": identifier,
        "key": identifier,
        "label": "Test measurement",
        "unit": unit,
        "value": value,
        "previous": previous,
        "change_abs": delta,
        "change_rel": delta / previous if previous else None,
        "change_pp": delta * 100 if unit == "share" and delta is not None else None,
        "significant": significant,
        "n": n,
        "side": "efficiency" if identifier == "E1" else "bottleneck",
        "baseline": None,
        "location": None,
        "extra": {},
        "ref": "/test",
        **extra,
    }


def validation_fixture():
    evidence = [
        entry("E1", 41.25, 35.1, n=512),
        entry("E15", 29, 20),
        entry("E22", 9, unit="count", extra={"weeks_total": 13}),
        entry("E53", 0.63, unit="share", location="area-Foo"),
    ]
    pack = {
        "period": {
            "from": "2026-01-01",
            "to": "2026-01-30",
            "days": 30,
            "compared_to": {"from": "2025-12-02", "to": "2025-12-31"},
        },
        "scope": {"repos": ["example/repo"]},
        "evidence": evidence,
        "observations": [{"evidence_ids": ["E1", "E15"]}],
        "hypotheses": [
            {
                "id": "H_review_capacity",
                "title": "Limited review capacity",
                "level": "high",
                "location": "area-Foo",
                "chain": {
                    "symptom": ["E1"],
                    "stage": ["E15"],
                    "location": ["E53"],
                    "mechanism": ["E22"],
                },
                "counter_evidence": [],
                "persistence": {"weeks_holding": 10, "weeks": 13},
                "ruled_out": [],
            }
        ],
    }
    output = {
        "narrative": "Median cycle time rose 18% to 41.3 h from 35.1 h [E1]. "
        "Limited review capacity is likely the main cause [E15][E22].",
        "hypotheses": [
            {
                "id": "H_review_capacity",
                "statement": "Limited review capacity is likely the main cause [E1][E15].",
            }
        ],
    }
    return deepcopy(pack), deepcopy(output)


def scoring_fixture():
    snapshot = golden()
    snapshot["series"] = {
        "previous": [{"pickup_p50_hours": v} for v in (16, 16, 24, 24)],
        "current": [{"pickup_p50_hours": v} for v in [29] * 10 + [19] * 3],
    }
    evidence = {
        "E1": entry("E1", 45, 30),
        "E15": entry("E15", 29, 20, n=61),
        "E18": entry("E18", 0.43, 0.35, unit="share"),
        "E22": entry("E22", 9, unit="count", extra={"weeks_total": 13}),
        "E24": entry("E24", 0.2, 0.19, unit="share"),
        "E30": entry("E30", 100, 100, unit="lines"),
        "E31": entry("E31", 0.1, 0.1, unit="share"),
        "E53": entry("E53", 0.63, unit="share", location="area-Foo"),
    }
    return snapshot, evidence
