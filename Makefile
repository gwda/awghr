.PHONY: all clean

ifndef VIRTUAL_ENV
$(error Missing virtual environment)
endif

all: .initialised
	python -m build

.initialised:
	pip install -r requirements-dev.txt
	pre-commit install
	date > .initialised

clean:
	git clean -Xdf
