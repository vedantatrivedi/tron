DEFAULT_PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
PYTHON ?= $(DEFAULT_PYTHON)
PYCACHE_PREFIX ?= .pycache
OPENENV_REF ?= c719decf2b19175d5ca35301d58a14c83e985480
OPENENV_PACKAGE ?= git+https://github.com/meta-pytorch/OpenEnv.git@$(OPENENV_REF)

.PHONY: test unit compile shellcheck ci docker-smoke openenv-install openenv-help openenv-check

test: unit compile shellcheck

unit:
	PYTHONPYCACHEPREFIX=$(PYCACHE_PREFIX) $(PYTHON) -m unittest discover -s tests -q

compile:
	PYTHONPYCACHEPREFIX=$(PYCACHE_PREFIX) $(PYTHON) -m py_compile \
		baseline/llm_agent.py \
		eval/demo.py \
		eval/run_eval.py \
		inference.py \
		tron/__init__.py \
		tron/action_analysis.py \
		tron/checks.py \
		tron/env.py \
		tron/executor.py \
		tron/incident_engine.py \
		tron/models.py \
		tron/observations.py \
		tron/oracle.py \
		tron/rewards.py \
		tron/runtime_setup.py \
		tron/sampler.py \
		tron/scenario_catalog.py \
		tron/scenarios/__init__.py \
		tron/scenarios/common.py \
		tron/scenarios/config_drift.py \
		tron/scenarios/service.py \
		tron/scenarios/resource.py \
		tron/scenarios/probe.py \
		tron/scenarios/network.py \
		tron/scenarios/ingress.py \
		tron/scenarios/deployment.py \
		tron/scenarios/compound.py \
		tron_openenv/__init__.py \
		tron_openenv/client.py \
		tron_openenv/models.py \
		tron_openenv/server/__init__.py \
		tron_openenv/server/environment.py \
		tron_openenv/server/app.py

shellcheck:
	bash -n setup.sh
	bash -n cleanup.sh
	bash -n app/test_client.sh
	bash -n scripts/container-entrypoint.sh
	bash -n scripts/install-k3s.sh
	bash -n scripts/openenv_check.sh
	bash -n scripts/provision-ec2.sh

ci: test

docker-smoke:
	docker build -t tron-ci .
	docker run --rm tron-ci make ci

openenv-install:
	$(PYTHON) -m pip install "$(OPENENV_PACKAGE)"

openenv-help:
	openenv --help

openenv-check:
	PYTHON=$(PYTHON) bash scripts/openenv_check.sh
