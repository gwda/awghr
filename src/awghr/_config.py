# SPDX-FileCopyrightText: 2024 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Workflow configuration."""

import os
from configparser import ConfigParser
from pathlib import Path
from shutil import which
from socket import getfqdn
from typing import Any, Dict, List, Optional, Tuple

from ._local import LocalWorkflowWriter
from ._pbs import PBSWorkflowWriter
from ._slurm import SlurmWorkflowWriter
from ._util import ConfigException

# Known batch systems:
# - name of batch system
# - description of batch system
# - characteristic executable used to identify batch system
# - function which writes workflow specific to batch system
_batch_systems: List[Tuple[str, str, str, Any]] = [
    ("pbs", "Portable Batch System and its derivatives", "qmgr", PBSWorkflowWriter),
    ("slurm", "Slurm Workload Manager", "scontrol", SlurmWorkflowWriter),
]

# General configuration keys and their default values
_general_cfg: Dict[str, Any] = {
    "account": None,
    "basedir": Path.cwd(),
    "envlist": "",
}

# Override host name for tests in test_02_config.py
_override_host = None


def read_config(
    src: Optional[str] = None,
    tags: Optional[List[str]] = None,
    select_batch_system: Optional[str] = None,
):
    """Read configuration."""

    cfg: Dict[str, Any] = {}
    batch_system_cfg: Dict[str, Any] = {}

    # Names of batch systems
    batch_system_names = set(batch_system for batch_system, _, _, _ in _batch_systems)

    # Determine batch system
    batch_keys = {}
    for batch_system, _, batch_exec, workflow_writer in _batch_systems:
        if which(batch_exec) is not None or batch_system == select_batch_system:
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
                msg = f"Both '{cfg['batch_system']}' and '{batch_system}' batch systems appear to be present"
                raise ConfigException(msg)
    if "batch_system" not in cfg:
        if select_batch_system is not None and select_batch_system != "local":
            msg = f"Batch system '{select_batch_system}' not found"
            raise ConfigException(msg)
        else:

            # If not batch system present, use local workflow writer
            cfg["batch_system"] = "local"
            cfg["workflow_writer"] = LocalWorkflowWriter

            # Default configuration for local workflow
            cfg["account"] = "local"

    # Read configuration
    parser = ConfigParser()
    if src is None:
        if "AWGHR_CFGFILE" in os.environ:
            src_file = Path(os.environ["AWGHR_CFGFILE"])
        else:
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
                        msg = f"Unknown batch system '{match}' in section '{section}'"
                        raise ConfigException(msg)
                    if match == cfg["batch_system"]:
                        batch_system_section = cfg["batch_system"]
                    else:
                        use_section = False

                elif target == "tag":

                    # If `tags` is empty, do not match any tagged section
                    if tags is None:
                        use_section = False

                    # `match` must be in `tags` (ignoring case)
                    else:
                        tags_lower = {tag.lower() for tag in tags}
                        if match.lower() not in tags_lower:
                            use_section = False

                else:
                    msg = f"Unknown section target '{target}'"
                    raise ConfigException(msg)

        if use_section:

            # Read non-`match` keys from configuration
            for key in parser[section]:
                if key.startswith("match "):
                    continue

                # Check for valid key
                if key not in _general_cfg and (
                    batch_system_section is None or key not in batch_keys
                ):
                    msg = f"Unknown section key '[{section}].{key}'"
                    raise ConfigException(msg)

                # Decide which dictionary to add key to
                if key in _general_cfg:
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
                        msg = f"Section key value '[{section}].{key}'='{val}' is not a valid boolean"
                        raise ConfigException(msg)
                    key_cfg[key] = val

                elif key.startswith("n_"):
                    try:
                        val = parser.getint(section, key)
                    except ValueError:
                        val = parser[section][key]
                        msg = f"Section key value '[{section}].{key}'='{val}' is not a valid integer"
                        raise ConfigException(msg)
                    key_cfg[key] = val

                elif key.endswith("dir"):
                    val = Path(parser[section][key])
                    try:
                        val = val.expanduser().resolve(strict=True)
                    except (FileNotFoundError, RuntimeError):
                        msg = f"Section key value '[{section}].{key}'='{val}' is not a directory"
                        raise ConfigException(msg)
                    key_cfg[key] = val

                elif key.endswith("list"):
                    val = parser[section][key].split()
                    vals = key_cfg.get(key, [])
                    for v in val:
                        if v not in vals:
                            vals.append(v)
                    key_cfg[key] = vals

                else:
                    val = parser[section][key]
                    key_cfg[key] = val

    # Check all keys are present
    for key in _general_cfg:
        if key not in cfg:
            cfg[key] = _general_cfg[key]
            if cfg[key] is None:
                msg = f"Configuration key '{key}' is undefined"
                raise ConfigException(msg)
    for key in batch_keys:
        if key not in batch_system_cfg:
            batch_system_cfg[key] = batch_keys[key]
            if batch_system_cfg[key] is None:
                msg = f"{cfg['batch_system']}-specific configuration key '{key}' is undefined"
                raise ConfigException()

    # Store batch system-specific configuration
    cfg["batch_system_cfg"] = batch_system_cfg

    return cfg


def show_config(args):
    """Show configuration."""

    # Helper function to print a dictionary with alignment
    def print_aligned(d):
        m = max(len(k) for k in d.keys())
        for k, v in sorted(d.items()):
            print(f"    {k:<{m}} : {v}")

    # Read configuration
    cfg = read_config(tags=args.tags, select_batch_system=args.batch)

    # Print supported batch systems
    print("Supported batch systems:")
    print_aligned(
        {
            batch_system: batch_system_desc
            for batch_system, batch_system_desc, _, _ in _batch_systems
            + [("local", "Runs workflow on local computer", "", None)]
        }
    )

    # Show general configuration
    print("General configuration:")
    print_aligned({k: cfg[k] for k in _general_cfg})

    # Show batch system configuration
    print(f"Batch system configuration for: {cfg['batch_system']}")
    if cfg["batch_system_cfg"]:
        print_aligned(cfg["batch_system_cfg"])
    else:
        print("    none")
