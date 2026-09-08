.PHONY: test venv clean regen-fixtures

VENV ?= .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

# Standalone parser test suite. Provisions a local venv on first run
# (pdfplumber, openpyxl, pytest, pillow), then runs pytest over tests/.
test: venv
	$(PY) -m pytest

venv: $(VENV)/.stamp

$(VENV)/.stamp: requirements-test.txt
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-test.txt
	touch $@

# Rebuild every committed synthetic fixture from its generator.
regen-fixtures: venv
	@for g in tests/generators/gen-*.py; do echo "=> $$g"; $(PY) "$$g"; done

clean:
	rm -rf $(VENV) .pytest_cache tests/**/__pycache__
