#!/usr/bin/env python3
"""Add encode-only soak columns to camera-fleet-health Grafana dashboard."""

import json
import sys

DASHBOARD_PATH = sys.argv[1] if len(sys.argv) > 1 else (
    "/home/ubuntu/monitoring/grafana-provisioning/dashboards/json/camera-fleet-health.json"
)
ALIAS_FILTER = "PiCam-Study|PiCam-Back|PiCam-MBR|PiCam-MBR2|PiCam-Outside"
JOB = "Raspberry Pi nodes"


def label_replace_expr(metric: str, ref_suffix: str) -> str:
    return (
        f'label_replace({metric}{{job="{JOB}", alias=~"{ALIAS_FILTER}"}}, '
        f'"ip", "$1", "instance", "(.*):.*")'
    )


def main():
    with open(DASHBOARD_PATH) as f:
        d = json.load(f)

    pan = d["panels"][1]
    existing_refs = {t.get("refId") for t in pan["targets"]}
    new_targets = [
        ("encsoak", "camera_agent_encode_only_soak"),
        ("encchunks", "camera_agent_encode_chunks"),
        ("encstale", "camera_agent_encode_stale_seconds"),
        ("noencode", "camera_agent_noencode_minutes"),
    ]
    for ref_id, metric in new_targets:
        if ref_id in existing_refs:
            continue
        pan["targets"].append({
            "datasource": {"type": "prometheus", "uid": "prometheus"},
            "expr": label_replace_expr(metric, ref_id),
            "format": "table",
            "instant": True,
            "refId": ref_id,
        })

    for t in pan.get("transformations", []):
        if t.get("id") != "organize":
            continue
        opts = t.get("options", {})
        if opts.get("renameByName"):
            rn = opts["renameByName"]
            rn["Value #encsoak"] = "Encode soak"
            rn["Value #encchunks"] = "Enc chunks"
            rn["Value #encstale"] = "Enc stale (s)"
            rn["Value #noencode"] = "NoEncode (m)"
            ex = opts.setdefault("excludeByName", {})
            for i in range(10, 14):
                for prefix in ("Time", "__name__", "instance", "job", "alias"):
                    ex[f"{prefix} {i}"] = True
        if opts.get("indexByName") and "Alias" in opts["indexByName"]:
            idx = opts["indexByName"]
            idx["Encode soak"] = 12
            idx["Enc chunks"] = 13
            idx["Enc stale (s)"] = 14
            idx["NoEncode (m)"] = 15

    if not any(p.get("title") == "Study encode stale (seconds)" for p in d["panels"]):
        d["panels"].append({
            "datasource": {"type": "prometheus", "uid": "prometheus"},
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "thresholds"},
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [
                            {"color": "green", "value": None},
                            {"color": "yellow", "value": 60},
                            {"color": "red", "value": 120},
                        ],
                    },
                    "unit": "s",
                },
                "overrides": [],
            },
            "gridPos": {"h": 4, "w": 12, "x": 12, "y": 10},
            "id": 99,
            "options": {
                "legend": {"displayMode": "list", "placement": "bottom"},
                "tooltip": {"mode": "single"},
            },
            "targets": [{
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "expr": f'camera_agent_encode_stale_seconds{{job="{JOB}", alias="PiCam-Study"}}',
                "legendFormat": "Study encode stale",
                "refId": "A",
            }],
            "title": "Study encode stale (seconds)",
            "type": "timeseries",
        })

    with open(DASHBOARD_PATH, "w") as f:
        json.dump(d, f, indent=2)
    print(f"Updated {DASHBOARD_PATH}")


if __name__ == "__main__":
    main()
