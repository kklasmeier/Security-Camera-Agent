#!/usr/bin/env python3
"""Add hang/recovery event panels to camera-fleet-health Grafana dashboard."""

import json
import sys

DASHBOARD_PATH = sys.argv[1] if len(sys.argv) > 1 else (
    "/home/ubuntu/monitoring/grafana-provisioning/dashboards/json/camera-fleet-health.json"
)
ALIAS_FILTER = "PiCam-Study|PiCam-Back|PiCam-MBR|PiCam-MBR2|PiCam-Outside"
JOB = "Raspberry Pi nodes"
DS = {"type": "prometheus", "uid": "prometheus"}


def label_replace_expr(metric: str) -> str:
    return (
        f'label_replace({metric}{{job="{JOB}", alias=~"{ALIAS_FILTER}"}}, '
        f'"ip", "$1", "instance", "(.*):.*")'
    )


def bump_y(panels, min_y, delta):
    for p in panels:
        g = p.get("gridPos") or {}
        if g.get("y", 0) >= min_y:
            g["y"] = g.get("y", 0) + delta
            p["gridPos"] = g


def main():
    with open(DASHBOARD_PATH) as f:
        d = json.load(f)

    panels = d["panels"]
    table = next(p for p in panels if p.get("id") == 2)

    new_table_targets = [
        ("hang24h", "camera_agent_hangs_last_24h"),
        ("recov24h", "camera_agent_recoveries_last_24h"),
        ("lasthang", "camera_agent_last_hang_timestamp"),
        ("lastrecov", "camera_agent_last_recovery_timestamp"),
    ]
    existing_refs = {t.get("refId") for t in table["targets"]}
    for ref_id, metric in new_table_targets:
        if ref_id in existing_refs:
            continue
        table["targets"].append({
            "datasource": DS,
            "expr": label_replace_expr(metric),
            "format": "table",
            "instant": True,
            "refId": ref_id,
        })

    for t in table.get("transformations", []):
        if t.get("id") != "organize":
            continue
        opts = t.get("options", {})
        rn = opts.setdefault("renameByName", {})
        rn["Value #hang24h"] = "Hangs (24h)"
        rn["Value #recov24h"] = "Recoveries (24h)"
        rn["Value #lasthang"] = "Last hang"
        rn["Value #lastrecov"] = "Last recovery"
        idx = opts.setdefault("indexByName", {})
        idx["Hangs (24h)"] = 16
        idx["Recoveries (24h)"] = 17
        idx["Last hang"] = 18
        idx["Last recovery"] = 19

    if not any(p.get("id") == 25 for p in panels):
        bump_y(panels, 50, 10)
        insert_y = 50
        panels.extend([
            {
                "collapsed": False,
                "gridPos": {"h": 1, "w": 24, "x": 0, "y": insert_y},
                "id": 25,
                "title": "Hang & recovery events",
                "type": "row",
            },
            {
                "datasource": DS,
                "description": "Spike when a node newly enters hang state (NoFrames / capture stuck).",
                "fieldConfig": {
                    "defaults": {
                        "color": {"mode": "palette-classic"},
                        "custom": {
                            "drawStyle": "bars",
                            "fillOpacity": 80,
                            "lineWidth": 1,
                            "stacking": {"mode": "none"},
                        },
                        "min": 0,
                        "unit": "short",
                    },
                    "overrides": [],
                },
                "gridPos": {"h": 8, "w": 12, "x": 0, "y": insert_y + 1},
                "id": 26,
                "options": {
                    "legend": {"displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "multi"},
                },
                "targets": [{
                    "datasource": DS,
                    "expr": (
                        f'sum by (alias) (increase(camera_agent_hang_events_total{{job="{JOB}", '
                        f'alias=~"{ALIAS_FILTER}"}}[$__rate_interval]))'
                    ),
                    "legendFormat": "{{alias}} hang",
                    "refId": "A",
                }],
                "title": "Hang events (new per scrape window)",
                "type": "timeseries",
            },
            {
                "datasource": DS,
                "description": "Spike when a node recovers (agent restart or natural recovery).",
                "fieldConfig": {
                    "defaults": {
                        "color": {"mode": "palette-classic"},
                        "custom": {
                            "drawStyle": "bars",
                            "fillOpacity": 80,
                            "lineWidth": 1,
                            "stacking": {"mode": "none"},
                        },
                        "min": 0,
                        "unit": "short",
                    },
                    "overrides": [],
                },
                "gridPos": {"h": 8, "w": 12, "x": 12, "y": insert_y + 1},
                "id": 27,
                "options": {
                    "legend": {"displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "multi"},
                },
                "targets": [{
                    "datasource": DS,
                    "expr": (
                        f'sum by (alias) (increase(camera_agent_recovery_events_total{{job="{JOB}", '
                        f'alias=~"{ALIAS_FILTER}"}}[$__rate_interval]))'
                    ),
                    "legendFormat": "{{alias}} recovery",
                    "refId": "A",
                }],
                "title": "Recovery events (new per scrape window)",
                "type": "timeseries",
            },
            {
                "datasource": DS,
                "description": "Red = agent unhealthy (NoFrames / hang). Green = healthy.",
                "fieldConfig": {
                    "defaults": {
                        "color": {"mode": "thresholds"},
                        "mappings": [
                            {"options": {"0": {"color": "red", "index": 0, "text": "DOWN"}},
                             "type": "value"},
                            {"options": {"1": {"color": "green", "index": 1, "text": "UP"}},
                             "type": "value"},
                        ],
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [
                                {"color": "red", "value": None},
                                {"color": "green", "value": 1},
                            ],
                        },
                    },
                    "overrides": [],
                },
                "gridPos": {"h": 6, "w": 24, "x": 0, "y": insert_y + 9},
                "id": 28,
                "options": {
                    "alignValue": "center",
                    "legend": {"displayMode": "list", "placement": "bottom"},
                    "mergeValues": True,
                    "rowHeight": 0.9,
                    "showValue": "never",
                    "tooltip": {"mode": "single"},
                },
                "targets": [{
                    "datasource": DS,
                    "expr": (
                        f'camera_agent_healthy{{job="{JOB}", alias=~"{ALIAS_FILTER}"}}'
                    ),
                    "legendFormat": "{{alias}}",
                    "refId": "A",
                }],
                "title": "Agent up/down timeline (all nodes)",
                "type": "state-timeline",
            },
        ])

    if not any(p.get("id") == 44 for p in panels):
        max_y = max(p.get("gridPos", {}).get("y", 0) for p in panels)
        drill_y = max_y + 6
        panels.extend([
            {
                "datasource": DS,
                "fieldConfig": {
                    "defaults": {
                        "color": {"mode": "palette-classic"},
                        "custom": {"drawStyle": "bars", "fillOpacity": 80},
                        "min": 0,
                    },
                    "overrides": [],
                },
                "gridPos": {"h": 6, "w": 12, "x": 0, "y": drill_y},
                "id": 44,
                "options": {
                    "legend": {"displayMode": "list", "placement": "bottom"},
                },
                "targets": [
                    {
                        "datasource": DS,
                        "expr": (
                            f'increase(camera_agent_hang_events_total{{job="{JOB}", alias="$camera"}}'
                            f'[$__rate_interval])'
                        ),
                        "legendFormat": "hang",
                        "refId": "A",
                    },
                    {
                        "datasource": DS,
                        "expr": (
                            f'increase(camera_agent_recovery_events_total{{job="{JOB}", alias="$camera"}}'
                            f'[$__rate_interval])'
                        ),
                        "legendFormat": "recovery",
                        "refId": "B",
                    },
                ],
                "title": "Hang & recovery events — $camera",
                "type": "timeseries",
            },
        ])

    # Timestamp columns as human-readable time in fleet table
    for p in panels:
        if p.get("id") != 2:
            continue
        overrides = p.setdefault("fieldConfig", {}).setdefault("overrides", [])
        for col in ("Last hang", "Last recovery"):
            overrides.append({
                "matcher": {"id": "byName", "options": col},
                "properties": [
                    {"id": "unit", "value": "dateTimeAsIso"},
                    {"id": "custom.cellOptions", "value": {"type": "auto"}},
                ],
            })

    with open(DASHBOARD_PATH, "w") as f:
        json.dump(d, f, indent=2)
    print(f"Updated {DASHBOARD_PATH}")


if __name__ == "__main__":
    main()
