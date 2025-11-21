# SPDX-FileCopyrightText: 2024 - 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

set -e

source /testing/test/.runnerrc

batch_system="$1"

log() {
    echo "===== $0: ${batch_system}: $@"
}

log "set up batch system"
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

log "set up virtual environment"
if test ! -f /testing/venv/bin/activate; then
    python3 -m venv /testing/venv
fi
source /testing/venv/bin/activate

log "install packages"
python3 -m pip install -r /testing/test/requirements-test.txt
python3 -m pip install --force-reinstall /testing/dist/*.whl

export COVERAGE_RCFILE="/testing/test/coverage.cfg"
export COVERAGE_FILE="/testing/.coverage-${batch_system}"
coverage erase

log "configuration"
(
    export AWGHR_CFGFILE="/testing/test/awghr.cfg"
    coverage run -a /testing/venv/bin/awghr config --batch="${batch_system}"
)

baseworkdir="/testing/workdir"
rm -rf "${baseworkdir}"

for TESTFILE in ${TESTFILES}; do
    TEST=`expr "X${TESTFILE}" : "^Xtest/test_[0-9][0-9]_\(.*\)\.py"`
    log "test: ${TEST}"
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
        log "PASS: ${TEST}"
    else
        log "FAIL: ${TEST}"
        exit 1
    fi
done

log "SUCCESS"
