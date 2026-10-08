# Common tasks (Linux/macOS). Windows equivalents are in docs/DEVELOPER_GUIDE.md.
PY ?= python3
NPM ?= npm

.PHONY: help install test lint benchmark stress engine desktop-install desktop-test e2e package release-check source-package licenses clean

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed 's/:.*## /\t/' | expand -t 20

install: ## Python engine + dev tools (pinned)
	$(PY) -m pip install -r requirements-dev.txt
	$(PY) -m pip install --no-deps -e .

test: ## Engine tests
	$(PY) -m pytest

lint: ## Static checks
	$(PY) -m pyflakes src tests packaging demo
	cd desktop && $(NPM) run typecheck

benchmark: ## Accuracy gate on all simulated profiles
	livestock-engine benchmark --runs 5 --profile standard --profile oblique --profile dense --profile harsh --profile sheep_pen

stress: ## Full raw-log pipeline at 55k / 110k / 221k detections
	livestock-engine stress --animals 7000 14000 28000

engine: ## Freeze the engine with PyInstaller into desktop/resources/engine (self-tested)
	$(PY) -m pip install -r requirements-build.txt
	$(PY) packaging/build_engine.py

desktop-install: ## Desktop app dependencies
	cd desktop && $(NPM) ci

desktop-test: ## Desktop unit tests
	cd desktop && $(NPM) test

e2e: ## Real-Electron end-to-end test (dev build; needs a display or xvfb-run)
	cd desktop && $(NPM) run build && $(NPM) run test:e2e

package: engine ## Installer for this OS (release/ in desktop/)
	cd desktop && $(NPM) run package

release-check: ## Verify the packaged app offline without Python
	cd desktop && $(NPM) run test:release

licenses: ## Regenerate docs/THIRD_PARTY_LICENSES.md
	$(PY) packaging/third_party_licenses.py

source-package: ## Clean source archive + checksums in dist/
	$(PY) packaging/make_source_package.py

clean:
	rm -rf build dist desktop/out desktop/release desktop/resources/engine .pytest_cache
