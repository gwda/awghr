from pathlib import Path


class ConfigException(Exception):
    """Raise for configuration errors"""


class BatchSystemException(Exception):
    """Raise for batch system errors"""


# Generate job directory from job ID:
# basedir
# └── subdirs 00/ to ff/ (max 256)
#     └── subdirs 00/ to ff/ (max 256)
#         └── subdirs of remaining job ID/
#             └── job files
def job_dir_from_ID(basedir: Path, job_ID):
    return basedir / job_ID[0:2] / job_ID[2:4] / job_ID[4:]


# Log progress at progressively longer intervals
class ProgressLogger:

    def __init__(self, logger, msg):
        self._logger = logger
        self._msg = msg
        self._count = 0
        self._print_prog_at = 7

    def update(self):
        self._count += 1
        if self._count == self._print_prog_at:
            self._logger.info(self._msg, str(self._count))
            self._print_prog_at *= 7

    def finish(self):
        self._logger.info(self._msg, "a total of " + str(self._count))


# Convenience class for writing lines to a text file
class FileLineWriter:

    def __init__(self, filepath: Path, *, append: bool = False):
        self._filepath = filepath
        self._mode = "a" if append else "w"

    def __enter__(self):
        self._file = self._filepath.open(self._mode)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        del self._file

    def __iadd__(self, line: str):
        print(str(line).strip(), file=self._file)
        return self
