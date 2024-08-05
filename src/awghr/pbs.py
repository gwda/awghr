import subprocess
from typing import Any, Dict

from .batchq import BatchQWorkflowWriter
from .util import BatchSystemException


# Write PBS workflow
class PBSWorkflowWriter(BatchQWorkflowWriter):

    # PBS-specific configuration keys
    KEYS: Dict[str, Any] = {}

    BATCHQ_HEADER = """
#PBS -N {jobname}
#PBS -A {account}
#PBS -l walltime={time_min}:00
#PBS -l mem={mem_MiB}MB
#PBS -l ncpus={threads}
#PBS -o {stdout}
#PBS -e {stderr}
#PBS -k doe
"""

    BATCHQ_JOB_ID = "PBS_JOBID"

    BATCHQ_ARRAY_INDEX = "PBS_ARRAY_INDEX"

    # - PBS insists that job arrays must have more than one subjob ...
    #   which we can get around by using a step larger than the range
    BATCHQ_ARRAY_SINGLETON = "0-1:2"

    BATCHQ_ARRAY_RANGE = "0-{n}"

    # - PBS does not support `{job_array_index}`, i.e. depending on job array
    #   subjobs. Everything still works since job arrays always contain jobs of
    #   the same dependency order, but the workflow will likely run slower,
    #   because it will have to wait for a whole job array to finish before
    #   running its dependents
    BATCHQ_DEPEND_ARRAY_INDEX_SUFFIX = "{batch_ID}"

    BATCHQ_SUBMIT_ARG_DEPEND = "-W depend="

    BATCHQ_SUBMIT_ARG_ARRAY = "-J "

    BATCHQ_SUBMIT_ARRAY_INDEX_REPL = "^array_index^"

    # - PBS provides no method to kill jobs with an unsatisfied dependency
    #   (because a prerequisite job failed), so must use `afterok` for
    #   prerequisite jobs here. This still works because dependent jobs will be
    #   run even if a prerequisite job fails (because of `afterany`) but will
    #   immediately fail if the `job_completed` files for their prerequisites do
    #   not exist. So the workflow will still fail gracefully, but slower,
    #   because all jobs will have to be run.
    BATCHQ_SUBMIT_ARRAY = "qsub"
    BATCHQ_SUBMIT_ARRAY_DEPEND = "afterany"

    BATCHQ_SUBMIT_STATE = "qsub"
    BATCHQ_SUBMIT_STATE_DEPEND = "afterany"

    # `qsub` returns `<job_ID>.<server>` for single jobs, and
    # `<job_array_ID>[].<server>` for job arrays. `<server>` is
    # optional and is removed for brevity.
    BATCHQ_ID_REMOVE_SUFFIX = "%%.*"

    BATCHQ_SUBMIT_ALL = """
qsub '{script}' >/dev/null
"""

    BATCHQ_QUEUE = """
( qstat -w -T `qselect -N '{jobname}'` 2>/dev/null ) || true
"""

    BATCHQ_CANCEL = """
qdel `qselect -N '{jobname}'` || true
"""

    def __init__(self, batchsyscfg):

        # Store location of job scratch directory
        self.jobfs = batchsyscfg["jobfs"]

        # Store maximum size of PBS job array
        if batchsyscfg["n_arraymax"] > 0:
            self.max_array_size = batchsyscfg["n_arraymax"]
        else:
            # Run `qmgr -c 'print server'` and parse its output
            retn = subprocess.run(
                ["qmgr", "-c", "print server"], capture_output=True, check=True
            )
            pbs_cfg = dict()
            for line in retn.stdout.decode("utf-8").splitlines():
                prefix = "set server "
                if line.startswith(prefix):
                    key, val = line[len(prefix) :].split("=", maxsplit=1)
                    pbs_cfg[key.strip()] = val.strip()

            # Get maximum size of PBS job array
            try:
                self.max_array_size = int(pbs_cfg["max_array_size"])
            except KeyError:  # pragma: no cover
                raise BatchSystemException(
                    "could not determine maximum size of PBS job array"
                )
