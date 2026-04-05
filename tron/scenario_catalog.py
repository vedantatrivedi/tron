from __future__ import annotations

"""Structured incident catalog for the tron benchmark."""

from tron.models import ScenarioTemplate
from tron.scenarios.compound import build_compound_scenarios
from tron.scenarios.config_drift import build_config_drift_scenarios
from tron.scenarios.deployment import build_deployment_scenarios
from tron.scenarios.ingress import build_ingress_scenarios
from tron.scenarios.network import build_network_scenarios
from tron.scenarios.probe import build_probe_scenarios
from tron.scenarios.resource import build_resource_scenarios
from tron.scenarios.service import build_service_scenarios


def load_catalog() -> list[ScenarioTemplate]:
    """Return the full incident catalog."""

    return [
        *build_config_drift_scenarios(),
        *build_service_scenarios(),
        *build_resource_scenarios(),
        *build_probe_scenarios(),
        *build_network_scenarios(),
        *build_ingress_scenarios(),
        *build_deployment_scenarios(),
        *build_compound_scenarios(),
    ]
