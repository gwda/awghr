# SPDX-FileCopyrightText: 2024 - 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

set -e

source /testing/test/.runnerrc
export _AWGHR_COLUMNS

batch_system="$1"

log() {
    echo "===== $0: ${batch_system}: $@"
}

log "set up batch system"
case "${batch_system}" in
    local)
        pkill -9 all_script.sh || true
        ;;
    htcondor)
        condor_rm -all -forcex || true
        ;;
    pbs)
        ( qdel `qselect` >/dev/null 2>&1 ) || true
        rm -rf /tmp/pbs-*
        sudo qmgr -c "set queue workq max_queuable = 1000"
        ;;
    slurm)
        scancel -u 0
        rm -rf /tmp/slurm-*
        sudo sacctmgr -i -Q add account default || true
        sudo sacctmgr -i -Q add user testuser Account=default || true
        sudo sacctmgr -i -Q modify user where name=testuser set DefaultAccount=default || true
        sudo sacctmgr -i -Q modify user testuser set MaxSubmitJobs=1000 || true
        ;;
esac

log "set up virtual environment"
if test ! -f /testing/venv/bin/activate; then
    python -m venv /testing/venv
fi
source /testing/venv/bin/activate

log "install packages"
pip install -r /testing/test/requirements-test.txt
pip install --force-reinstall /testing/dist/*.whl
rm -rf /testing/dist/

export COVERAGE_RCFILE="/testing/test/coverage.cfg"
export COVERAGE_FILE="/testing/.coverage-${batch_system}"
coverage erase

log "configuration"
(
    export AWGHR_CFGFILE="/testing/test/awghr.cfg"
    coverage run -a /testing/venv/bin/awghr-config --batch="${batch_system}"
)

baseworkdir="/testing/workdir"
rm -rf "${baseworkdir}"

for TESTFILE in ${TESTFILES}; do
    TEST=`expr "X${TESTFILE}" : "^Xtest/test_[0-9][0-9]_\(.*\)\.py"`
    log "test: ${TEST}"
    workdir="${baseworkdir}/${TEST}"
    mkdir -p "${workdir}"
    cp "/testing/test/awghr.cfg" "${workdir}/.awghr.cfg"
    sed '/^account *=/d' "${workdir}/.awghr.cfg" >"${workdir}/.awghr-no-account.cfg"
    (
        exec > >(trap "" INT TERM; sed "s/^/${batch_system}:${TEST}  | /")
        exec 2> >(trap "" INT TERM; sed "s/^/${batch_system}:${TEST} E| /" >&2)
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
