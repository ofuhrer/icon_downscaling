.DEFAULT_GOAL := help
PYTHON ?= python3

.PHONY: help externals install-dev test syntax check

help:
	@echo "make externals    initialize HICAR"
	@echo "make install-dev  install Python test dependencies"
	@echo "make test         run the focused coordinator tests"
	@echo "make check        run syntax and whitespace checks"

externals:
	./scripts/bootstrap_externals.sh

install-dev:
	$(PYTHON) -m pip install -r requirements/dev.txt

test:
	$(PYTHON) -m pytest -q

syntax:
	./scripts/check_repository.sh --syntax-only

check:
	./scripts/check_repository.sh
