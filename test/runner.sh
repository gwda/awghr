set -e

source /tmp/test/.runnerrc

batch_system="$1"

echo "===== $0: set up batch system: ${batch_system}"
case "${batch_system}" in
    pbs)
        ( qdel `qselect` >/dev/null 2>&1 ) || true
        rm -rf /tmp/pbs-scratch/
        mkdir -p /tmp/pbs-scratch/
        ;;
    slurm)
        scancel -u 0
        sed -i '/^MaxArraySize/d' /usr/local/etc/slurm.conf
        echo "MaxArraySize = 7" >> /usr/local/etc/slurm.conf
        scontrol reconfigure
        rm -rf /tmp/slurm-scratch/
        mkdir -p /tmp/slurm-scratch/
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
export COVERAGE_FILE="/tmp/.coverage-${batch_system}"
coverage erase

testworkbasedir="/tmp/workdirs"
rm -rf "${testworkbasedir}"

if test "X${TESTS}" != X; then
    echo "===== $0: run single test"
else
    echo "===== $0: run all tests"
    TESTS=`cat /tmp/test/list-of-tests.txt`
fi

for TEST in ${TESTS}; do
    echo "===== $0: test: ${TEST}"
    testworkdir="${testworkbasedir}/${TEST}"
    mkdir -p "${testworkdir}"
    cp "/tmp/test/awghr.cfg" "${testworkdir}/.awghr.cfg"
    (
        exec > >(trap "" INT TERM; sed "s/^/${TEST}: /")
        exec 2> >(trap "" INT TERM; sed "s/^/${TEST} ERROR: /" >&2)
        cd "${testworkdir}"
        export HOME="${testworkdir}"
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

echo "===== $0: SUCCESS for ${batch_system}"
