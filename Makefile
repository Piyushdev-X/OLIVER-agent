# OLIVER AI Coding Harness - standard evaluation interface
#
#   export AI_API_KEY="<PROVIDED_API_KEY>"
#   make setup    install and configure every dependency (into .venv/)
#   make run      launch OLIVER in evaluation mode (interactive console)
#   make test     offline test suite and scripted evaluation
#   make clean    remove generated artefacts
#
# The credential is read from the AI_API_KEY environment variable at run time.
# It is never written to this file, to configuration files or to logs, and the
# recipe lines that hand it to the harness are silent, so make never prints it.
# The model and every run setting are defined in configuration-files/oliver.toml.
#
# Optional variables:
#   make run REPO=<path or git URL> ISSUE=<file or GitHub issue URL> [TEST_CMD="..."]
#                                  solve one task without prompts
#   make test REAL=1               also score the configured model on the bundled tasks
#   make setup PYTHON=python3.12   choose the interpreter (default: first Python 3.10-3.14 on PATH)

VENV := .venv
VENV_PYTHON := $(VENV)/bin/python
SETUP_STAMP := $(VENV)/.oliver-setup-complete
REQUIREMENTS := dependency-files/requirements.txt
SOURCE := $(CURDIR)/source-code
PIP_BOOTSTRAP := https://bootstrap.pypa.io/get-pip.py
VERSION_CHECK := import sys; sys.exit(not (3, 10) <= sys.version_info[:2] <= (3, 14))

ifeq ($(origin PYTHON),undefined)
PYTHON := $(shell for candidate in python3.12 python3.13 python3.11 python3.10 python3.14 python3 python; do \
    if command -v $$candidate >/dev/null 2>&1 && $$candidate -c '$(VERSION_CHECK)' >/dev/null 2>&1; then \
    echo $$candidate; break; fi; done)
endif

RUN_ARGS := $(if $(REPO),--repo "$(REPO)") $(if $(ISSUE),--issue "$(ISSUE)") $(if $(TEST_CMD),--test-command "$(TEST_CMD)")

.DEFAULT_GOAL := help
.PHONY: help setup run test clean

help:
	@echo "OLIVER AI Coding Harness"
	@echo ""
	@echo "  export AI_API_KEY=\"<PROVIDED_API_KEY>\""
	@echo "  make setup    install and configure every dependency"
	@echo "  make run      launch the harness in evaluation mode"
	@echo "  make test     run the test suite and the offline evaluation"
	@echo "  make clean    remove generated artefacts"
	@echo ""
	@echo "Model and run settings: configuration-files/oliver.toml"

setup:
	@echo "Setting up OLIVER..."
	@if [ -z "$(PYTHON)" ] || ! "$(PYTHON)" -c '$(VERSION_CHECK)' >/dev/null 2>&1; then \
		echo "ERROR: OLIVER needs Python 3.10 to 3.14 and none was found (PYTHON=$(PYTHON))."; \
		echo "       Install one, or name it: make setup PYTHON=/path/to/python3.12"; \
		exit 1; \
	fi
	@command -v git >/dev/null 2>&1 || { echo "ERROR: git is required (OLIVER checkpoints every edit with git)."; exit 1; }
	@if [ ! -x "$(VENV_PYTHON)" ] || ! "$(VENV_PYTHON)" -m pip --version >/dev/null 2>&1; then \
		echo "  Creating the virtual environment $(VENV)/ with $(PYTHON)"; \
		rm -rf "$(VENV)"; \
		if ! "$(PYTHON)" -m venv "$(VENV)" >/dev/null 2>&1; then \
			echo "  This Python has no ensurepip (python3-venv); bootstrapping pip instead"; \
			rm -rf "$(VENV)"; \
			"$(PYTHON)" -m venv --without-pip "$(VENV)" && \
			"$(VENV_PYTHON)" -c "import urllib.request; urllib.request.urlretrieve('$(PIP_BOOTSTRAP)', '$(VENV)/bootstrap-pip.py')" && \
			"$(VENV_PYTHON)" "$(VENV)/bootstrap-pip.py" --quiet || exit 1; \
		fi; \
	fi
	@echo "  Installing dependencies from $(REQUIREMENTS)"
	@"$(VENV_PYTHON)" -m pip install --quiet --disable-pip-version-check -r "$(REQUIREMENTS)"
	@AI_API_KEY="$$AI_API_KEY" PYTHONPATH="$(SOURCE)" "$(VENV_PYTHON)" -m oliver --check
	@touch "$(SETUP_STAMP)"
	@echo "Setup complete. Next: make run"

$(SETUP_STAMP): $(REQUIREMENTS)
	@$(MAKE) --no-print-directory setup

run: $(SETUP_STAMP)
	@echo "Starting OLIVER AI Harness..."
	@AI_API_KEY="$$AI_API_KEY" PYTHONPATH="$(SOURCE)" "$(VENV_PYTHON)" -m oliver $(RUN_ARGS)

test: $(SETUP_STAMP)
	@echo "Running tests (offline: scripted mock model, the key is withheld)..."
	@AI_API_KEY= PATH="$(CURDIR)/$(VENV)/bin:$$PATH" "$(VENV_PYTHON)" -m pytest -c configuration-files/pytest.ini --rootdir=source-code/tests source-code/tests
	@echo "Running the evaluation tasks$(if $(REAL), with the configured model,)..."
	@AI_API_KEY="$$AI_API_KEY" PATH="$(CURDIR)/$(VENV)/bin:$$PATH" "$(VENV_PYTHON)" source-code/eval/run_eval.py $(if $(REAL),--real)

clean:
	@echo "Removing generated artefacts..."
	rm -rf runs workspace $(VENV)
	find source-code -name __pycache__ -type d -prune -exec rm -rf {} +
	@echo "Done. make setup installs the dependencies again."
