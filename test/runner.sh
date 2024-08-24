set -e

source /testing/test/.runnerrc

batch_system="$1"

echo "===== $0: set up batch system: ${batch_system}"
case "${batch_system}" in
    pbs)
        ( qdel `qselect` >/dev/null 2>&1 ) || true
        rm -rf /tmp/pbs-*
        ;;
    slurm)
        scancel -u 0
        rm -rf /tmp/slurm-*
        ;;
esac

echo "===== $0: set up virtual environment"
if test ! -f /testing/venv/bin/activate; then
    python3 -m venv /testing/venv
fi
source /testing/venv/bin/activate

echo "===== $0: install packages"
pip install -r /testing/test/requirements-test.txt
pip install --force-reinstall /testing/dist/*.whl

export COVERAGE_RCFILE="/testing/test/coverage.cfg"
export COVERAGE_FILE="/testing/.coverage-${batch_system}"
coverage erase

baseworkdir="/testing/workdir"
rm -rf "${baseworkdir}"

if test "X${TESTS}" != X; then
    echo "===== $0: run single test"
else
    echo "===== $0: run all tests"
    TESTS=`cat /testing/test/list-of-tests.txt`
fi

for TEST in ${TESTS}; do
    echo "===== $0: test: ${TEST}"
    workdir="${baseworkdir}/${TEST}"
    mkdir -p "${workdir}"
    cp "/testing/test/awghr.cfg" "${workdir}/.awghr.cfg"
    (
        exec > >(trap "" INT TERM; sed "s/^/${TEST}: /")
        exec 2> >(trap "" INT TERM; sed "s/^/${TEST} ERROR: /" >&2)
        cd "${workdir}"
        export HOME="${workdir}"
        coverage run -a "/testing/test/test_${TEST}.py"
    )
    status=$?
    if test ${status} -eq 0; then
        echo "===== $0: PASS: ${TEST}"
    else
        echo "===== $0: FAIL: ${TEST}"
        exit 1
    fi
done

echo "===== $0: SUCCESS for ${batch_system}"
