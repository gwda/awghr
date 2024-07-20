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
	docker create --name "awghr-testing-$*" "kwwette/testing-batch-system:$*" /bin/bash /tmp/test/runner.sh
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
	-docker stop "awghr-testing-$*"
	docker cp "dist/." "awghr-testing-$*:/tmp/dist"
	@echo "$(TEST)" > "test/.single-test.txt"
	docker cp "test/." "awghr-testing-$*:/tmp/test"
	docker start "awghr-testing-$*" -ai
	-docker stop "awghr-testing-$*"

.PHONY: clean
clean:
	git clean -Xdf
