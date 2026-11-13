"""serviceConfig from chart v0.1.7: also pullPolicy, replicas, autoscaling, probes, plainEnv, command.

Pure functions only (no Git, Vault or cluster).
"""
import pytest
import yaml

from app.services import helm_values

CHART = yaml.safe_dump({"services": [
    {"name": "bridge", "enabled": True, "image": {"repository": "reg/bridge", "tag": "v1", "pullPolicy": "Always"},
     "replicas": 2, "ports": [{"containerPort": 3000}], "env": {"A": "x"},
     "resources": {"requests": {"cpu": "100m"}},
     "autoscaling": {"enabled": True, "minReplicas": 1, "maxReplicas": 3, "targetCPUUtilizationPercentage": 70},
     "probes": {"readinessProbe": {"httpGet": {"path": "/health", "port": 3000}, "periodSeconds": 10}},
     "plainEnv": {"NODE_ENV": "development"}, "command": ["node", "server.js"]},
    {"name": "idle", "image": {"repository": "reg/idle", "tag": "v1", "pullPolicy": "IfNotPresent"}, "replicas": 0},
    {"name": "off", "enabled": False, "image": {"repository": "reg/off", "tag": "v1"}},
]})


@pytest.mark.parametrize("version, expected", [("v0.1.6", False), ("v0.1.7", True), ("v0.1.10", True),
                                               ("v0.2.0", True), (None, False)])
def test_which_releases_accept_the_extra_keys(version, expected):
    assert helm_values.supports_full_service_config(version) is expected


def test_default_output_is_unchanged_for_older_charts():
    cfg = helm_values.chart_service_defaults(CHART)
    assert cfg["bridge"] == {"image": {"repository": "reg/bridge", "tag": "v1"}, "resources": {"requests": {"cpu": "100m"}}}
    assert "off" not in cfg


def test_full_adds_the_tunable_settings():
    cfg = helm_values.chart_service_defaults(CHART, full=True)
    b = cfg["bridge"]
    assert b["image"]["pullPolicy"] == "Always"
    assert b["replicas"] == 2
    assert b["autoscaling"]["maxReplicas"] == 3
    assert b["probes"]["readinessProbe"]["periodSeconds"] == 10
    assert b["plainEnv"] == {"NODE_ENV": "development"}
    assert b["command"] == ["node", "server.js"]
    for wiring in ("ports", "env"):                  # stays in the chart
        assert wiring not in b
    assert cfg["idle"]["replicas"] == 0              # an explicit 0 is kept
    assert "off" not in cfg


def test_disabled_services_are_left_out():
    assert "bridge" not in helm_values.chart_service_defaults(CHART, ["bridge"], full=True)


def test_a_probe_is_merged_as_one_value():
    new = {"bridge": {"probes": {"readinessProbe": {"exec": {"command": ["true"]}}}}}
    old = {"bridge": {"probes": {"readinessProbe": {"httpGet": {"path": "/health"}}}}}
    # untouched since the old release: follows the new default, with no httpGet left behind
    assert helm_values.merge_service_config(new, old, old)["bridge"]["probes"]["readinessProbe"] == {"exec": {"command": ["true"]}}
    # edited by hand: the whole probe is kept
    edited = {"bridge": {"probes": {"readinessProbe": {"tcpSocket": {"port": 1}}}}}
    assert helm_values.merge_service_config(new, edited, old)["bridge"]["probes"]["readinessProbe"] == {"tcpSocket": {"port": 1}}


def test_a_tenants_edits_survive_and_new_keys_are_added_when_the_chart_moves_up():
    old_defaults = helm_values.chart_service_defaults(CHART)                  # what a v0.1.6 tenant file holds
    new_defaults = helm_values.chart_service_defaults(CHART, full=True)
    existing = {"bridge": {**old_defaults["bridge"], "resources": {"requests": {"cpu": "500m"}}}}
    out = helm_values.merge_service_config(new_defaults, existing, old_defaults)["bridge"]
    assert out["resources"]["requests"]["cpu"] == "500m"       # kept
    assert out["replicas"] == 2 and out["autoscaling"]["enabled"] is True      # added


def test_edited_replicas_and_autoscaling_are_kept_on_rewrite():
    defaults = helm_values.chart_service_defaults(CHART, full=True)
    existing = {"bridge": {"replicas": 5, "autoscaling": {"enabled": False}}}
    out = helm_values.merge_service_config(defaults, existing)["bridge"]
    assert out["replicas"] == 5
    assert out["autoscaling"]["enabled"] is False
    assert out["autoscaling"]["maxReplicas"] == 3              # untouched keys still present


def test_rendered_section_is_valid_yaml():
    section = helm_values.append_service_config("a: 1\n", helm_values.chart_service_defaults(CHART, full=True))
    assert yaml.safe_load(section)["serviceConfig"]["bridge"]["command"] == ["node", "server.js"]
