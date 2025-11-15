# SPDX-FileCopyrightText: 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Manage Docker containers needed for testing."""

import argparse
import inspect
import subprocess
from pathlib import Path
from subprocess import DEVNULL


def run(*cmd, check=True, **kwargs):
    """Run a command."""
    print("---", "command:", *cmd)
    return subprocess.run(cmd, check=check, **kwargs)


def build(docker_files):
    """Build Docker images."""

    for docker_file in docker_files:
        name = docker_file.stem

        run(
            "docker",
            "buildx",
            "build",
            "--allow",
            "network.host",
            "--build-arg",
            f"BUILDKIT_SANDBOX_HOSTNAME={name}-ubuntu-host",
            "--add-host",
            f"{name}-ubuntu-host:host-gateway",
            "--file",
            str(docker_file),
            "--tag",
            f"kwwette/testing-batch-system:{name}",
            str(docker_file.parent),
        )


def push(docker_files):
    """Push Docker images."""

    run("docker", "login")

    try:

        for docker_file in docker_files:
            name = docker_file.stem

            run("docker", "push", f"kwwette/testing-batch-system:{name}")

    finally:

        run("docker", "logout")


def init(docker_files):
    """Create Docker containers for testing."""

    for docker_file in docker_files:
        name = docker_file.stem

        run(
            "docker",
            "stop",
            f"awghr-testing-{name}",
            check=False,
            stdout=DEVNULL,
            stderr=DEVNULL,
        )

        run(
            "docker",
            "rm",
            f"awghr-testing-{name}",
            check=False,
            stdout=DEVNULL,
            stderr=DEVNULL,
        )

        run(
            "docker",
            "create",
            "-it",
            "--hostname",
            f"{name}-ubuntu-host",
            "--name",
            f"awghr-testing-{name}",
            f"kwwette/testing-batch-system:{name}",
            "/bin/bash",
            "/testing/test/runner.sh",
            name,
            stdout=DEVNULL,
        )


def debug(docker_file):
    """Debug a Docker container."""

    name = docker_file.stem

    image = run(
        "docker",
        "commit",
        f"awghr-testing-{name}",
        capture_output=True,
        encoding="utf-8",
    ).stdout.strip()

    try:
        run(
            "docker",
            "run",
            "-it",
            "--hostname",
            f"{name}-ubuntu-host",
            "--entrypoint",
            "/usr/local/bin/entrypoint",
            image,
            "/bin/bash",
        )

    finally:
        run(
            "docker",
            "image",
            "rm",
            image,
            check=False,
            stdout=DEVNULL,
            stderr=DEVNULL,
        )


def test(docker_files, test_scripts):
    """Run tests inside Docker containers."""

    runnerrc = Path("test") / ".runnerrc"
    runnerrc.write_text(
        "TESTFILES='{}'\n".format(" ".join(str(p) for p in test_scripts))
    )

    for docker_file in docker_files:
        name = docker_file.stem

        run(
            "docker",
            "stop",
            f"awghr-testing-{name}",
            check=False,
            stdout=DEVNULL,
            stderr=DEVNULL,
        )

        for d in ("dist", "test"):
            run(
                "docker",
                "cp",
                f"{d}/.",
                f"awghr-testing-{name}:/testing/{d}",
                stdout=DEVNULL,
            )

        run("docker", "start", "-ai", f"awghr-testing-{name}")

        run(
            "docker",
            "cp",
            f"awghr-testing-{name}:/testing/.coverage-{name}",
            f"test/.coverage-{name}",
            stdout=DEVNULL,
        )

        run(
            "docker",
            "stop",
            f"awghr-testing-{name}",
            check=False,
            stdout=DEVNULL,
            stderr=DEVNULL,
        )


docker_files = {path.stem: path for path in Path("docker").glob("*.Dockerfile")}
all_docker_files = [docker_files["local"]] + sorted(
    [path for name, path in docker_files.items() if name != "local"]
)

test_scripts = {path.stem[8:]: path for path in Path("test").glob("test_*.py")}
all_test_scripts = sorted(test_scripts.values())

parser = argparse.ArgumentParser()
subparsers = parser.add_subparsers(dest="command")
subparsers.required = True
for command in (build, push, init, debug, test):

    cmdparser = subparsers.add_parser(
        command.__name__, description=command.__doc__, help=command.__doc__
    )
    cmdparser.set_defaults(command=command)
    for cmdarg in inspect.signature(command).parameters.keys():

        if cmdarg == "docker_files":
            cmdparser.add_argument(
                cmdarg,
                type=lambda k: docker_files[k],
                nargs="*",
                default=all_docker_files,
                help="Names of Dockerfiles in docker/",
            )
        elif cmdarg == "docker_file":
            cmdparser.add_argument(
                cmdarg,
                type=lambda k: docker_files[k],
                help="Name of a Dockerfile in docker/",
            )
        elif cmdarg == "test_scripts":
            cmdparser.add_argument(
                "--tests",
                dest=cmdarg,
                type=lambda k: test_scripts[k],
                nargs="+",
                default=all_test_scripts,
                help="Names of test scripts in test/",
            )
        else:
            raise ValueError(cmdarg)

args = vars(parser.parse_args())
command = args["command"]
del args["command"]
command(**args)
