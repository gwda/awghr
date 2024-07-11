ifndef VIRTUAL_ENV
$(error Missing virtual environment)
endif

BATCH_SYSTEMS = slurm

INIT_DOCKER_TARGETS = $(addprefix .initialised-docker-,$(BATCH_SYSTEMS))
INIT_TARGETS = .initialised-dev $(INIT_DOCKER_TARGETS)

.PHONY: build
build: $(INIT_TARGETS)
	-rm -f dist/*.tar.* dist/*.whl
	python -m build

.PHONY: init
init:
	@-rm -f $(INIT_TARGETS)
	@$(MAKE) $(INIT_TARGETS)

.initialised-dev: Makefile
	pip install -r requirements-dev.txt
	pre-commit install
	@date > $@

$(INIT_DOCKER_TARGETS): .initialised-docker-%: Makefile
	-docker stop awghr-testing-$*
	-docker rm awghr-testing-$*
	docker create --name "awghr-testing-$*" "kwwette/testing-batch-system:$*" /bin/bash ./test/runner.sh
	@date > $@

.PHONY: pretty
pretty:
	pre-commit run -a

CHECK_TARGETS = $(addprefix check-,$(BATCH_SYSTEMS))

.PHONY: check
check: build $(CHECK_TARGETS)

.PHONY: $(CHECK_TARGETS)
$(CHECK_TARGETS): check-%:
	-docker stop "awghr-testing-$*"
	docker cp "dist/." "awghr-testing-$*:/tmp/dist"
	docker cp "test/." "awghr-testing-$*:/tmp/test"
	docker start "awghr-testing-$*" -ai
	-docker stop "awghr-testing-$*"

.PHONY: clean
clean:
	git clean -Xdf
