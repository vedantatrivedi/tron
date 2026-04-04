from __future__ import annotations

"""Seeded scenario selection and parameter rendering."""

from dataclasses import replace
import random

from models import RepairCheck, ScenarioInstance, ScenarioTemplate


def _render(value: str, params: dict[str, object]) -> str:
    rendered = value
    for key, param_value in params.items():
        rendered = rendered.replace(f"{{{key}}}", str(param_value))
    return rendered


def _render_checks(checks: list[RepairCheck], params: dict[str, object]) -> list[RepairCheck]:
    rendered: list[RepairCheck] = []
    for check in checks:
        rendered.append(
            RepairCheck(
                name=_render(check.name, params),
                command=[_render(part, params) for part in check.command],
                success_substring=_render(check.success_substring, params),
                match_mode=check.match_mode,
            )
        )
    return rendered


def _resolve_coupled_parameters(
    template: ScenarioTemplate,
    params: dict[str, object],
) -> dict[str, object]:
    if template.id != "networkpolicy-plus-secondary-drift":
        return params

    variant = params["secondary_variant"]
    if variant == "stale config":
        params.update(
            {
                "secondary_inject_command": (
                    "kubectl -n tron patch configmap app-config --type merge -p "
                    "'{\"data\":{\"REDIS_HOST\":\"redis-shadow\"}}' && "
                    "kubectl -n tron rollout restart deployment/nginx && "
                    "kubectl -n tron rollout status deployment/nginx --timeout=120s && "
                    "kubectl -n tron patch configmap app-config --type merge -p "
                    "'{\"data\":{\"REDIS_HOST\":\"redis\"}}'"
                ),
                "secondary_activation_script": (
                    "kubectl -n tron exec deployment/nginx -c redis-bridge -- printenv REDIS_HOST"
                ),
                "secondary_activation_expected": "redis-shadow",
                "secondary_repair_script": (
                    "kubectl -n tron exec deployment/nginx -c redis-bridge -- printenv REDIS_HOST"
                ),
                "secondary_repair_expected": "redis",
            }
        )
    else:
        params.update(
            {
                "secondary_inject_command": (
                    "kubectl -n tron patch service redis --type merge -p "
                    "'{\"spec\":{\"selector\":{\"app\":\"redis-shadow\"}}}'"
                ),
                "secondary_activation_script": (
                    "kubectl -n tron get service redis -o jsonpath='{.spec.selector.app}'"
                ),
                "secondary_activation_expected": "redis-shadow",
                "secondary_repair_script": (
                    "kubectl -n tron get service redis -o jsonpath='{.spec.selector.app}'"
                ),
                "secondary_repair_expected": "redis",
            }
        )
    return params


def get_scenario(catalog: list[ScenarioTemplate], scenario_id: str) -> ScenarioTemplate:
    """Return a scenario template by id."""

    for template in catalog:
        if template.id == scenario_id:
            return template
    raise KeyError(f"unknown scenario id: {scenario_id}")


def sample_scenario(
    catalog: list[ScenarioTemplate],
    seed: int,
    scenario_id: str | None = None,
) -> ScenarioInstance:
    """Choose a scenario template and render its seeded parameters."""

    rng = random.Random(seed)
    template = get_scenario(catalog, scenario_id) if scenario_id else rng.choice(catalog)
    chosen_parameters = {
        key: rng.choice(values) for key, values in sorted(template.parameters.items())
    }
    chosen_parameters = _resolve_coupled_parameters(template, chosen_parameters)
    rendered_inject_commands = [_render(cmd, chosen_parameters) for cmd in template.inject_commands]
    rendered_restore_commands = [_render(cmd, chosen_parameters) for cmd in template.restore_commands]
    recent_changes = [
        f"incident={template.id}",
        f"difficulty={template.difficulty}",
        _render(template.trigger_context, chosen_parameters),
        *[_render(line, chosen_parameters) for line in template.recent_change_templates],
    ]
    instance = ScenarioInstance(
        template=replace(
            template,
            activation_checks=_render_checks(template.activation_checks, chosen_parameters),
            repair_checks=_render_checks(template.repair_checks, chosen_parameters),
        ),
        seed=seed,
        chosen_parameters=chosen_parameters,
        rendered_inject_commands=rendered_inject_commands,
        rendered_restore_commands=rendered_restore_commands,
        recent_changes=recent_changes,
    )
    return instance
