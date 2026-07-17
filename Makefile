PYTHON   := python3.14
COFFERDAM_SRC := ../cofferdam/src
PKG_DIR  := /tmp/cofferdam_app-dev-pkgs
PYPATH   := $(PKG_DIR):$(COFFERDAM_SRC):$(CURDIR)

.PHONY: install test lint typecheck check

install:
	$(PYTHON) -m pip install --target $(PKG_DIR) -e ../cofferdam --no-deps -q
	$(PYTHON) -m pip install --target $(PKG_DIR) -e ".[dev]" --no-deps -q
	$(PYTHON) -m pip install --target $(PKG_DIR) pytest pytest-cov mypy ruff -q

test:
	PYTHONPATH=$(PYPATH) $(PYTHON) -m pytest $(ARGS)

lint:
	PYTHONPATH=$(PYPATH) $(PYTHON) -m ruff check cofferdam_app tests

typecheck:
	PYTHONPATH=$(PYPATH) $(PYTHON) -m mypy

check: lint typecheck test
