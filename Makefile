.DEFAULT_GOAL := help

PYTHON ?= python3
HICAR_CONTRACT_TESTS := \
	tests/test_hicar_output_metadata.py \
	tests/test_hicar_restart_initialization.py

.PHONY: help externals install-dev balfrin-preflight recovery-audit recovery-archive-verify test test-hicar-contract test-all syntax check

RECOVERY_ARCHIVE_MANIFEST ?= /store_new/mch/msopr/olifu/icon_downscaling/recovery/v1/manifests/archive-foundation-v1.json
RECOVERY_REPORTS_MANIFEST ?= /store_new/mch/msopr/olifu/icon_downscaling/recovery/v1/manifests/archive-qualification-reports-v1.json

help:
	@echo "ICON-to-HICAR coordinator"
	@echo "  make externals   initialize the pinned public HICAR source"
	@echo "  make install-dev install local validation/test dependencies"
	@echo "  make balfrin-preflight"
	@echo "                    verify this checkout and Balfrin dependencies"
	@echo "  make recovery-audit"
	@echo "                    audit cold-start deletion readiness"
	@echo "  make recovery-archive-verify"
	@echo "                    read back and verify the durable recovery archive"
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

balfrin-preflight:
	bash -lc '[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh; \
		module use "$${USER_ENV_ROOT:-/mch-environment/v8}/modules"; \
		module load python/3.11.7; \
		python ./scripts/balfrin_preflight.py $(if $(CHECK_FDB),--check-fdb,)'

recovery-audit:
	./scripts/check_recovery_readiness.sh

recovery-archive-verify:
	$(PYTHON) ./scripts/verify_recovery_archive.py \
		--manifest "$(RECOVERY_ARCHIVE_MANIFEST)"
	$(PYTHON) ./scripts/verify_recovery_archive.py \
		--manifest "$(RECOVERY_REPORTS_MANIFEST)"

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
