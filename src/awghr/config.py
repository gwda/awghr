import os
from configparser import ConfigParser
from pathlib import Path
from shutil import which
from socket import getfqdn
from typing import Any, Callable, Dict, List, Optional, Tuple

from .slurm import SlurmWorkflowWriter
from .util import ConfigException

# known batch systems:
# - name of batch system
# - characteristic executable used to identify batch system
# - function which writes workflow specific to batch system
_batch_systems: List[Tuple[str, str, Optional[Callable]]] = [
    ("slurm", "sbatch", SlurmWorkflowWriter)
]

# known configuration keys and their default values
_keys: Dict[str, Any] = {
    "account": None,
    "basedir": Path.cwd(),
    "dated?": True,
    "slurm:jobfs": None,
}

# override host name for tests in test_config.py
_override_host = None


def read_config(src: Optional[str] = None, tags: Optional[List[str]] = None):

    cfg: Dict[str, Any] = dict()

    # determine batch system
    for batch_system, batch_exec, workflow_writer in _batch_systems:
        if which(batch_exec) is not None:
            if "batch_system" not in cfg:
                cfg["batch_system"] = batch_system
                cfg["workflow_writer"] = workflow_writer
            else:
                raise ConfigException(
                    f"Both '{cfg['batch_system']}' and '{batch_system}' batch systems appear to be present"
                )

    # names of batch systems
    batch_system_names = set(
        batch_system for batch_system, batch_exec, workflow_writer in _batch_systems
    )

    # read configuration
    parser = ConfigParser()
    if src is None:
        src_file = Path(os.environ["HOME"]) / ".awghr.cfg"
        parser.read(src_file, encoding="utf-8")
    else:
        parser.read_string(src)

    # parse sections
    batch_system_cfg = dict()
    for section in parser:
        use_section = True
        batch_system_section = None
        if section != parser.default_section:

            # read `match` keys from configuration
            for key in parser[section]:
                if not key.startswith("match "):
                    continue
                _, target = key.split(maxsplit=1)
                match = parser[section][key]

                if target == "host":
                    host = getfqdn() if _override_host is None else _override_host

                    # `match` starting with "." must match end of host name
                    # `match` containing "." (but not at start/end) must match whole host name
                    # otherwise `match` must match start of host name
                    if match.startswith("."):
                        if not host.endswith(match):
                            use_section = False
                    elif (
                        "." in match
                        and not match.startswith(".")
                        and not match.endswith(".")
                    ):
                        if host != match:
                            use_section = False
                    else:
                        if not host.startswith(match):
                            use_section = False

                elif target == "batch":

                    # `match` must be a known match system
                    if match not in batch_system_names:
                        raise ConfigException(
                            f"Unknown batch system '{match}' in section '{section}'"
                        )
                    if match == cfg["batch_system"]:
                        batch_system_section = cfg["batch_system"]
                    else:
                        use_section = False

                elif target == "tag":

                    # if `tags` is empty, do not match any tagged section
                    if tags is None:
                        use_section = False

                    # `match` must be in `tags`
                    elif match not in tags:
                        use_section = False
                else:
                    raise ConfigException(f"Unknown section target '{target}'")

        if use_section:

            # read non-`match` keys from configuration
            for key in parser[section]:
                if key.startswith("match "):
                    continue

                # check for valid key
                if key not in _keys and (
                    batch_system_section is None
                    or f"{batch_system_section}:{key}" not in _keys
                ):
                    raise ConfigException(f"Unknown section key '[{section}].{key}'")

                # parse value of key
                val: Any
                if key.endswith("?"):
                    try:
                        val = parser.getboolean(section, key)
                    except ValueError:
                        val = parser[section][key]
                        raise ConfigException(
                            f"Section key value '[{section}].{key}'='{val}' is not a valid boolean"
                        )

                elif key.endswith("dir"):
                    val = Path(parser[section][key])
                    try:
                        val = val.resolve(strict=True)
                    except (FileNotFoundError, RuntimeError):
                        raise ConfigException(
                            f"Section key value '[{section}].{key}'='{val}' is not a directory"
                        )

                else:
                    val = parser[section][key]

                # assign value to common/batch system-specific configuration
                if val is not None:
                    if key in _keys:
                        cfg[key] = val
                    else:
                        batch_system_cfg[key] = val

    # check all keys are present
    for key in _keys:
        if key.startswith(cfg["batch_system"] + ":"):
            batch_system_key = key.split(":", maxsplit=1)[1]
            if batch_system_key not in batch_system_cfg:
                batch_system_cfg[batch_system_key] = _keys[key]
                if batch_system_cfg[batch_system_key] is None:
                    raise ConfigException(
                        f"{cfg['batch_system']}-specific configuration key '{batch_system_key}' is undefined"
                    )
        elif key not in cfg:
            cfg[key] = _keys[key]
            if cfg[key] is None:
                raise ConfigException(f"Configuration key '{key}' is undefined")

    # store batch system-specific configuration
    cfg["batch_system_cfg"] = batch_system_cfg

    return cfg
