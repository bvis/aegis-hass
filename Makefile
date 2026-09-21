.PHONY: setup build-docker check test test-e2e lint format format-check typecheck dead-code proto cli

DOCKER_IMAGE = aegis-ajax-dev
DOCKER_RUN = docker run --rm -v $(PWD):/app -w /app $(DOCKER_IMAGE)

setup:
	git config --local core.hooksPath .githooks
	@echo "Git hooks configured (core.hooksPath = .githooks); pre-push now runs lint, format, typecheck, dead-code and tests in the dev image."

build-docker:
	docker build -f Dockerfile.dev -t $(DOCKER_IMAGE) .

check: lint format-check typecheck test dead-code
	@echo "All checks passed."

test:
	pytest tests/unit/ -v --cov=custom_components/aegis_ajax --cov-fail-under=80 --cov-report=term-missing

test-e2e:
	pytest tests/e2e/ -v -m "e2e and not destructive"

lint:
	ruff check .

format:
	ruff format .

format-check:
	ruff format --check .

typecheck:
	mypy custom_components/aegis_ajax/ --ignore-missing-imports --exclude 'proto/'

dead-code:
	vulture custom_components/aegis_ajax/ vulture_whitelist.py --exclude custom_components/aegis_ajax/proto/

# PROTOS is optional and takes paths relative to proto_src/. Pass it to
# regenerate only what changed instead of churning the whole tree:
#   make proto PROTOS="systems/ajax/.../hub_device.proto"
proto:
	bash scripts/compile_protos.sh $(PROTOS)

cli:
	python scripts/test_connection.py
