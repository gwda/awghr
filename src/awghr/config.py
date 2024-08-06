import os
from configparser import ConfigParser
from pathlib import Path
from shutil import which
from socket import getfqdn
from typing import Any, Dict, List, Optional, Tuple

from .pbs import PBSWorkflowWriter
from .slurm import SlurmWorkflowWriter
from .util import ConfigException

# Known batch systems:
# - name of batch system
# - characteristic executable used to identify batch system
# - function which writes workflow specific to batch system
_batch_systems: List[Tuple[str, str, Any]] = [
    ("pbs", "qmgr", PBSWorkflowWriter),
    ("slurm", "scontrol", SlurmWorkflowWriter),
]

# Configuration keys and their default values
_keys: Dict[str, Any] = {
    "account": None,
    "basedir": Path.cwd(),
    "dated?": True,
    "envlist": "",
}

# Override host name for tests in test_config.py
_override_host = None


def read_config(src: Optional[str] = None, tags: Optional[List[str]] = None):

    cfg: Dict[str, Any] = dict()
    batch_system_cfg: Dict[str, Any] = dict()

    # Names of batch systems
    batch_system_names = set(
        batch_system for batch_system, batch_exec, workflow_writer in _batch_systems
    )

    # Determine batch system
    batch_keys = dict()
    for batch_system, batch_exec, workflow_writer in _batch_systems:
        if which(batch_exec) is not None:
            if "batch_system" not in cfg:
                cfg["batch_system"] = batch_system
                cfg["workflow_writer"] = workflow_writer
                if workflow_writer is not None:
                    for cls in workflow_writer.mro():
                        if hasattr(cls, "KEYS"):
                            for batch_key, batch_key_val in getattr(
                                cls, "KEYS"
                            ).items():
                                assert batch_key not in batch_keys
                                batch_keys[batch_key] = batch_key_val
            else:
                raise ConfigException(
                    f"Both '{cfg['batch_system']}' and '{batch_system}' batch systems appear to be present"
                )
    if "batch_system" not in cfg:
        batch_systems_str = "/".join(batch_system_names)
        raise ConfigException(f"No known batch system ({batch_systems_str}) was found")

    # Read configuration
    parser = ConfigParser()
    if src is None:
        src_file = Path(os.environ["HOME"]) / ".awghr.cfg"
        parser.read(src_file, encoding="utf-8")
    else:
        parser.read_string(src)

    # Parse sections
    for section in parser:
        use_section = True
        batch_system_section = None
        if section != parser.default_section:

            # Read `match` keys from configuration
            for key in parser[section]:
                if not key.startswith("match "):
                    continue
                _, target = key.split(maxsplit=1)
                match = parser[section][key]

                if target == "host":
                    host = getfqdn() if _override_host is None else _override_host

                    # `match` starting with "." must match end of host name
                    # `match` containing "." (but not at start/end) must match whole host name
                    # Otherwise `match` must match start of host name
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

                    # If `tags` is empty, do not match any tagged section
                    if tags is None:
                        use_section = False

                    # `match` must be in `tags`
                    elif match not in tags:
                        use_section = False
                else:
                    raise ConfigException(f"Unknown section target '{target}'")

        if use_section:

            # Read non-`match` keys from configuration
            for key in parser[section]:
                if key.startswith("match "):
                    continue

                # Check for valid key
                if key not in _keys and (
                    batch_system_section is None or key not in batch_keys
                ):
                    raise ConfigException(f"Unknown section key '[{section}].{key}'")

                # Decide which dictionary to add key to
                if key in _keys:
                    key_cfg = cfg
                else:
                    key_cfg = batch_system_cfg

                # Parse value of key and assign to dictionary
                val: Any
                if key.endswith("?"):
                    try:
                        val = parser.getboolean(section, key)
                    except ValueError:
                        val = parser[section][key]
                        raise ConfigException(
                            f"Section key value '[{section}].{key}'='{val}' is not a valid boolean"
                        )
                    key_cfg[key] = val

                elif key.startswith("n_"):
                    try:
                        val = parser.getint(section, key)
                    except ValueError:
                        val = parser[section][key]
                        raise ConfigException(
                            f"Section key value '[{section}].{key}'='{val}' is not a valid integer"
                        )
                    key_cfg[key] = val

                elif key.endswith("dir"):
                    val = Path(parser[section][key])
                    try:
                        val = val.resolve(strict=True)
                    except (FileNotFoundError, RuntimeError):
                        raise ConfigException(
                            f"Section key value '[{section}].{key}'='{val}' is not a directory"
                        )
                    key_cfg[key] = val

                elif key.endswith("list"):
                    val = parser[section][key].split()
                    vals = key_cfg.get(key, list())
                    for v in val:
                        if v not in vals:
                            vals.append(v)
                    key_cfg[key] = vals

                else:
                    val = parser[section][key]
                    key_cfg[key] = val

    # Check all keys are present
    for key in _keys:
        if key not in cfg:
            cfg[key] = _keys[key]
            if cfg[key] is None:
                raise ConfigException(f"Configuration key '{key}' is undefined")
    for key in batch_keys:
        if key not in batch_system_cfg:
            batch_system_cfg[key] = batch_keys[key]
            if batch_system_cfg[key] is None:
                raise ConfigException(
                    f"{cfg['batch_system']}-specific configuration key '{key}' is undefined"
                )

    # Store batch system-specific configuration
    cfg["batch_system_cfg"] = batch_system_cfg

    return cfg
