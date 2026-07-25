.DEFAULT_GOAL := help

PYTHON ?= python3
HICAR_CONTRACT_TESTS := \
	tests/test_hicar_output_metadata.py \
	tests/test_hicar_restart_initialization.py

.PHONY: help externals install-dev recovery-audit test test-hicar-contract test-all syntax check

help:
	@echo "ICON-to-HICAR coordinator"
	@echo "  make externals   initialize the pinned public HICAR source"
	@echo "  make install-dev install local validation/test dependencies"
	@echo "  make recovery-audit"
	@echo "                    audit cold-start deletion readiness"
	@echo "  make test        run the portable coordinator regression suite"
	@echo "  make test-hicar-contract"
	@echo "                    check the active HICAR source-development contract"
	@echo "  make test-all    run portable and HICAR source-contract tests"
	@echo "  make syntax      check tracked Python and shell syntax"
	@echo "  make check       run syntax, whitespace, and lint checks"

externals:
	./scripts/bootstrap_externals.sh

install-dev:
	$(PYTHON) -m pip install -r requirements/dev.txt

recovery-audit:
	./scripts/check_recovery_readiness.sh

test:
	$(PYTHON) -m pytest -q \
		--ignore=tests/test_hicar_output_metadata.py \
		--ignore=tests/test_hicar_restart_initialization.py

test-hicar-contract:
	$(PYTHON) -m pytest -q $(HICAR_CONTRACT_TESTS)

test-all:
	$(PYTHON) -m pytest -q

syntax:
	./scripts/check_repository.sh --syntax-only

check:
	./scripts/check_repository.sh
