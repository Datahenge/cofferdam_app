PYTHON   := python3.14
COFFERDAM_SRC := ../cofferdam/src
PKG_DIR  := /tmp/cofferdam_app-dev-pkgs
PYPATH   := $(PKG_DIR):$(COFFERDAM_SRC):$(CURDIR)

# Bench-env toolchain: use when this app lives inside a deployed frappe-bench
# (cofferdam is already installed in ../../env; no sibling source checkout).
# Run `bench-install` once, then `make bench-check`.
BENCH_PY   := $(CURDIR)/../../env/bin/python
BENCH_RUFF := $(CURDIR)/../../env/bin/ruff

.PHONY: install test lint typecheck check bench-install bench-test bench-lint bench-typecheck bench-check

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

# --- Bench-env targets (run against the deployed frappe-bench virtualenv) ---

bench-install:
	$(BENCH_PY) -m pip install pytest==9.1.1 pytest-cov==7.1.0 mypy==2.3.0 ruff==0.14.10

bench-test:
	PYTHONPATH=$(CURDIR) $(BENCH_PY) -m pytest $(ARGS)

bench-lint:
	$(BENCH_RUFF) check cofferdam_app tests

bench-typecheck:
	PYTHONPATH=$(CURDIR) $(BENCH_PY) -m mypy

bench-check: bench-lint bench-typecheck bench-test
