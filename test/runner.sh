# SPDX-FileCopyrightText: 2024 - 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

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
python3 -m pip install -r /testing/test/requirements-test.txt
python3 -m pip install --force-reinstall /testing/dist/*.whl

export COVERAGE_RCFILE="/testing/test/coverage.cfg"
export COVERAGE_FILE="/testing/.coverage-${batch_system}"
coverage erase

baseworkdir="/testing/workdir"
rm -rf "${baseworkdir}"

for TESTFILE in ${TESTFILES}; do
    TEST=`expr "X${TESTFILE}" : "^Xtest/test_[0-9][0-9]_\(.*\)\.py"`
    echo "===== $0: test: ${TEST}"
    workdir="${baseworkdir}/${TEST}"
    mkdir -p "${workdir}"
    cp "/testing/test/awghr.cfg" "${workdir}/.awghr.cfg"
    (
        exec > >(trap "" INT TERM; sed "s/^/${TEST}: /")
        exec 2> >(trap "" INT TERM; sed "s/^/${TEST} ERROR: /" >&2)
        cd "${workdir}"
        export HOME="${workdir}"
        coverage run -a "/testing/${TESTFILE}"
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
