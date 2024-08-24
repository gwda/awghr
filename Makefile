ifndef VIRTUAL_ENV
$(error Missing virtual environment)
endif

BATCH_SYSTEMS = local slurm pbs

.PHONY: build
build: .initialised-dev
	-rm -f dist/*.tar.* dist/*.whl
	python -m build

.initialised-dev: requirements-dev.txt Makefile
	pip install -r requirements-dev.txt
	pre-commit install
	@date > $@

.PHONY: pretty
pretty:
	pre-commit run -a

CHECK_TARGETS = $(addprefix check-,$(BATCH_SYSTEMS))

.PHONY: check
check: $(CHECK_TARGETS)

ifndef TEST
check:
	coverage combine --rcfile=test/coverage.cfg --data-file=.coverage test/.coverage-*
	coverage report --rcfile=test/coverage.cfg --data-file=.coverage
endif

.PHONY: .pre-check
.pre-check: build
	@for testpy in test/test_*.py; do \
		test=`expr "X$${testpy}" : 'Xtest/test_\(.*\)\.py'`; \
		if ! grep -qF "$${test}" test/list-of-tests.txt; then \
			echo "ERROR: test $${test} not in test/list-of-tests.txt"; \
			exit 1; \
		fi; \
	done
	@for test in `cat test/list-of-tests.txt`; do \
		if ! test -f "test/test_$${test}.py"; then \
			echo "ERROR: test test/test_$${test}.py does not exist"; \
			exit 1; \
		fi; \
	done
	@if test "X$(TEST)" != X && ! test -f "test/test_$(TEST).py"; then \
		echo "ERROR: test test/test_$(TEST).py does not exist"; \
		exit 1; \
	fi

.PHONY: $(CHECK_TARGETS)
$(CHECK_TARGETS): check-%: .pre-check .initialised-docker-%
	-docker stop "awghr-testing-$*" >/dev/null
	docker cp "dist/." "awghr-testing-$*:/testing/dist" >/dev/null
	@echo "TESTS=$(TEST)" > "test/.runnerrc"
	docker cp "test/." "awghr-testing-$*:/testing/test" >/dev/null
	docker start -ai "awghr-testing-$*"
	docker cp "awghr-testing-$*:/testing/.coverage-$*" "test/.coverage-$*" >/dev/null
	-docker stop "awghr-testing-$*" >/dev/null

INIT_DOCKER_TARGETS = $(addprefix .initialised-docker-,$(BATCH_SYSTEMS))

$(INIT_DOCKER_TARGETS): .initialised-docker-%: Makefile
	-docker stop awghr-testing-$* >/dev/null
	-docker rm awghr-testing-$* >/dev/null
	docker create -it \
		--hostname "$*-ubuntu-host" \
		--name "awghr-testing-$*" \
		"kwwette/testing-batch-system:$*" \
		/bin/bash /testing/test/runner.sh "$*" \
		>/dev/null
	@date > $@

DEBUG_CHECK_TARGETS = $(addprefix debug-,$(CHECK_TARGETS))

.PHONY: $(DEBUG_CHECK_TARGETS)
$(DEBUG_CHECK_TARGETS): debug-check-%: .initialised-docker-%
	@image=`docker commit awghr-testing-$*`; \
	docker run -it --hostname "$*-ubuntu-host" --rm --entrypoint /usr/local/bin/entrypoint "$${image}" /bin/bash; \
	docker image rm "$${image}" >/dev/null

DOCKER_IMAGES = $(BATCH_SYSTEMS) htcondor
BUILD_DOCKER_TARGETS = $(addprefix docker-,$(DOCKER_IMAGES))

.PHONY: docker
docker: $(BUILD_DOCKER_TARGETS)

.PHONY: $(BUILD_DOCKER_TARGETS)
$(BUILD_DOCKER_TARGETS): docker-%:
	docker buildx build \
		--allow network.host \
		--build-arg "BUILDKIT_SANDBOX_HOSTNAME=$*-ubuntu-host" \
		--add-host "$*-ubuntu-host:host-gateway" \
		--file "docker/$*.Dockerfile" \
		--tag "kwwette/testing-batch-system:$*" \
		docker/

.PHONY: docker-push
docker-push:
	docker login
	for tag in $(DOCKER_IMAGES); do docker push "kwwette/testing-batch-system:$${tag}" || break; done
	-docker logout

.PHONY: clean
clean:
	git clean -Xdf
