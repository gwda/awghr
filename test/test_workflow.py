import logging
import sys
from pathlib import Path

from awghr import Workflow

# Set up logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(levelname)s %(asctime)s %(name)s : %(message)s",
)
logger = logging.getLogger(__name__)

# Create workflow
w = Workflow(name="add_job", dated=False)

# Try creating a workflow with a non-existent base directory
try:
    Workflow(name="dummy", basedir=Path("/nonexistent"))
    raise AssertionError("should have raised a ValueError")
except ValueError:
    pass

# Add job
job_args = dict(
    args=dict(index=0),
    cmds=[["true"]],
    time=1 * w.MIN,
    mem=10 * w.MiB,
    disk=10 * w.MiB,
    outputs=["out_{index}.txt"],
)
w.add_job(**job_args)

# Try to add a duplicate job
try:
    w.add_job(**job_args)
    raise AssertionError("should have raised a ValueError")
except ValueError:
    pass

# Try to add a job with non-existent executable
try:
    w.add_job(
        args=dict(index=999),
        cmds=[["./does-not-exist"]],
        time=1 * w.MIN,
        mem=10 * w.MiB,
        disk=10 * w.MiB,
        outputs=["dummy.txt"],
    )
    raise AssertionError("should have raised a ValueError")
except ValueError:
    pass

# Try to add a job with non-unique inputs
try:
    w.add_job(
        args=dict(index=999),
        cmds=[["true"]],
        time=1 * w.MIN,
        mem=10 * w.MiB,
        disk=10 * w.MiB,
        inputs=[Path("./dummy.txt"), Path("./dummy.txt")],
        outputs=["dummy.txt"],
    )
    raise AssertionError("should have raised a ValueError")
except ValueError:
    pass

# Try to add a job with non-existent inputs
try:
    w.add_job(
        args=dict(index=999),
        cmds=[["true"]],
        time=1 * w.MIN,
        mem=10 * w.MiB,
        disk=10 * w.MiB,
        inputs=[Path("./non-existent.txt")],
        outputs=["dummy.txt"],
    )
    raise AssertionError("should have raised a ValueError")
except ValueError:
    pass

# Try to add a job with non-existent dependency
try:
    w.add_job(
        args=dict(index=999),
        cmds=[["true"]],
        time=1 * w.MIN,
        mem=10 * w.MiB,
        disk=10 * w.MiB,
        inputs=[("non-existent-job", "dummy")],
        outputs=["dummy.txt"],
    )
    raise AssertionError("should have raised a ValueError")
except ValueError:
    pass

# Try to add a job with non-unique outputs
try:
    w.add_job(
        args=dict(index=999),
        cmds=[["true"]],
        time=1 * w.MIN,
        mem=10 * w.MiB,
        disk=10 * w.MiB,
        outputs=["dummy.txt", "dummy.txt"],
    )
    raise AssertionError("should have raised a ValueError")
except ValueError:
    pass

# Try to add a job with non-workflow-unique final output file
bad_job = w.add_job(
    args=dict(index=999),
    cmds=[["true"]],
    time=1 * w.MIN,
    mem=10 * w.MiB,
    disk=10 * w.MiB,
    outputs=["out_0.txt"],
)
try:
    w.write()
    raise AssertionError("should have raised a ValueError")
except ValueError:
    pass
finally:
    del w._jobs[bad_job]

# Try to write workflow when a final output file already exists
existing_outfile = Path(".") / "add_job" / "outputs" / "out_0.txt"
existing_outfile.parent.mkdir(parents=True, exist_ok=True)
existing_outfile.write_text("already here!")
try:
    w.write()
    raise AssertionError("should have raised a ValueError")
except ValueError:
    pass
finally:
    existing_outfile.unlink()

w.write()
