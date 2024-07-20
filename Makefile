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
	-docker stop awghr-testing-$* >/dev/null
	-docker rm awghr-testing-$* >/dev/null
	docker create -it --hostname "$*-ubuntu-host" --name "awghr-testing-$*" "kwwette/testing-batch-system:$*" /bin/bash /tmp/test/runner.sh "$*" >/dev/null
	@date > $@

.PHONY: pretty
pretty:
	pre-commit run -a

CHECK_TARGETS = $(addprefix check-,$(BATCH_SYSTEMS))

.PHONY: check
check: build $(CHECK_TARGETS)

.PHONY: .pre-check
.pre-check:
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
	@if test -f "test/test_coverage.py"; then \
		echo "ERROR: 'coverage' is a special test target"; \
		exit 1; \
	fi
	@if test "X$(TEST)" != X && test "X$(TEST)" != Xcoverage && ! test -f "test/test_$(TEST).py"; then \
		echo "ERROR: test test/test_$(TEST).py does not exist"; \
		exit 1; \
	fi

.PHONY: $(CHECK_TARGETS)
$(CHECK_TARGETS): check-%: .pre-check
	-docker stop "awghr-testing-$*" >/dev/null
	docker cp "dist/." "awghr-testing-$*:/tmp/dist" >/dev/null
	@echo "$(TEST)" > "test/.single-test.txt"
	docker cp "test/." "awghr-testing-$*:/tmp/test" >/dev/null
	docker start -ai "awghr-testing-$*"
	-docker stop "awghr-testing-$*" >/dev/null

DEBUG_CHECK_TARGETS = $(addprefix debug-,$(CHECK_TARGETS))

.PHONY: $(DEBUG_CHECK_TARGETS)
$(DEBUG_CHECK_TARGETS): debug-check-%:
	@image=`docker commit awghr-testing-$*`; \
	docker run -it --hostname "$*-ubuntu-host" --rm --entrypoint /usr/local/bin/entrypoint "$${image}" /bin/bash; \
	docker image rm "$${image}" >/dev/null

DOCKER_IMAGES = htcondor slurm
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
