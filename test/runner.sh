set -e

batch_system="$1"

echo "===== $0: set up batch system: ${batch_system}"
case "${batch_system}" in
    slurm)
        scancel -u 0
        sed -i '/^MaxArraySize/d' /usr/local/etc/slurm.conf
        echo "MaxArraySize = 7" >> /usr/local/etc/slurm.conf
        scontrol reconfigure
        ;;
esac

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

export BATCH_SUBMISSION=yes
TESTS=`cat /tmp/test/.single-test.txt`
if test "X${TESTS}" != X; then

    if test "X${TESTS}" = Xcoverage; then
        echo "===== $0: check test coverage only"
        TESTS=`cat /tmp/test/list-of-tests.txt`
        BATCH_SUBMISSION=no
        report_coverage=yes
    else
        echo "===== $0: run single test"
        report_coverage=no
    fi

else
    echo "===== $0: run all tests"
    TESTS=`cat /tmp/test/list-of-tests.txt`
    report_coverage=yes
fi

for TEST in ${TESTS}; do
    echo "===== $0: test: ${TEST}"
    scratchdir="/tmp/scratch/${TEST}"
    mkdir -p "${scratchdir}"
    cp "/tmp/test/awghr.cfg" "${scratchdir}/.awghr.cfg"
    (
        exec > >(trap "" INT TERM; sed "s/^/${TEST}: /")
        exec 2> >(trap "" INT TERM; sed "s/^/${TEST} ERROR: /" >&2)
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

if test "${report_coverage}" = yes; then
    echo "===== $0: report coverage"
    coverage report
fi

echo "===== $0: SUCCESS"
