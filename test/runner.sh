set -e

echo "===== $0: set up virtual environment"
if test ! -f /tmp/venv/bin/activate; then
    python3 -m venv /tmp/venv
fi
source /tmp/venv/bin/activate

echo "===== $0: install packages"
pip install -r /tmp/test/requirements-test.txt
pip install --force-reinstall /tmp/dist/*.whl

export COVERAGE_RCFILE="/tmp/test/coverage.cfg"
export COVERAGE_FILE="/tmp/.coverage"
coverage erase

scratchbasedir="/tmp/scratch"
rm -rf "${scratchbasedir}"

echo "===== $0: run tests"
for TEST in `cat /tmp/test/list-of-tests.txt`; do
    echo "===== $0: test: ${TEST}"
    scratchdir="/tmp/scratch/${TEST}"
    mkdir -p "${scratchdir}"
    cp "/tmp/test/awghr.cfg" "${scratchdir}/.awghr.cfg"
    (
        cd "${scratchdir}"
        export HOME="${scratchdir}"
        coverage run -a "/tmp/test/test_${TEST}.py"
    )
    status=$?
    if test ${status} -eq 0; then
        echo "===== $0: PASS: ${TEST}"
    else
        echo "===== $0: FAIL: ${TEST}"
        exit 1
    fi
done

echo "===== $0: report coverage"
coverage report

echo "===== $0: SUCCESS"
