# SPDX-FileCopyrightText: 2024 - 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

"""Test workflow configuration."""

from pathlib import Path

import awghr._config
from awghr._cli import cli
from awghr._config import ConfigException, _batch_systems, read_config

default_src = """
[DEFAULT SLURM]
match batch = slurm
jobfs = /tmp
[DEFAULT PBS]
match batch = pbs
jobfs = /tmp
"""


def test(f):
    print("----- {}: unit test {}".format(Path(__file__).name, f.__name__), flush=True)
    f()


@test
def multiple_batch_system():
    try:
        _batch_systems.append(("dummy1", "dummy", "true", None))
        _batch_systems.append(("dummy2", "dummy", "false", None))
        read_config("")
        msg = "should have raised a ConfigException"
        raise AssertionError(msg)
    except ConfigException:
        pass
    finally:
        del _batch_systems[-1]
        del _batch_systems[-1]


@test
def nonexistent_batch_system():
    try:
        read_config("", select_batch_system="dummy")
        msg = "should have raised a ConfigException"
        raise AssertionError(msg)
    except ConfigException:
        pass


@test
def read_config_file():
    cfg = read_config()
    assert cfg["account"] == "default"


@test
def default_section():
    src = default_src
    src += """
[DEFAULT]
account = acct
"""
    cfg = read_config(src)
    assert cfg["account"] == "acct"


@test
def invalid_section_target():
    src = default_src
    src += """
[invalid_section_target]
match dummy = xyz
account = acct
"""
    try:
        read_config(src)
        msg = "should have raised a ConfigException"
        raise AssertionError(msg)
    except ConfigException:
        pass


@test
def invalid_section_key():
    src = default_src
    src += """
[DEFAULT]
dummy = xyz
"""
    try:
        read_config(src)
        msg = "should have raised a ConfigException"
        raise AssertionError(msg)
    except ConfigException:
        pass


@test
def parsing_bool():
    try:
        _batch_systems = awghr._config._batch_systems
        awghr._config._batch_systems = [("dummy", "dummy", "true", None)]
        _general_cfg = awghr._config._general_cfg
        awghr._config._general_cfg = {"why?": None, "why not?": None, "could be?": True}
        src = """
[DEFAULT]
why? = true
why not? = no
"""
        cfg = read_config(src)
        assert cfg["why?"]
        assert not cfg["why not?"]
        assert cfg["could be?"]
    finally:
        awghr._config._batch_systems = _batch_systems
        awghr._config._general_cfg = _general_cfg

    try:
        _batch_systems = awghr._config._batch_systems
        awghr._config._batch_systems = [("dummy", "dummy", "true", None)]
        _general_cfg = awghr._config._general_cfg
        awghr._config._general_cfg = {"why?": None}
        src = """
[DEFAULT]
why? = because
"""
        cfg = read_config(src)
        msg = "should have raised a ConfigException"
        raise AssertionError(msg)
    except ConfigException:
        pass
    finally:
        awghr._config._batch_systems = _batch_systems
        awghr._config._general_cfg = _general_cfg


@test
def parsing_int():
    try:
        _batch_systems = awghr._config._batch_systems
        awghr._config._batch_systems = [("dummy", "dummy", "true", None)]
        _general_cfg = awghr._config._general_cfg
        awghr._config._general_cfg = {"n_letters": None, "n_words": None, "n_pages": 3}
        src = """
[DEFAULT]
n_letters = 6
n_words = 23
"""
        cfg = read_config(src)
        assert cfg["n_letters"] == 6
        assert cfg["n_words"] == 23
        assert cfg["n_pages"] == 3
    finally:
        awghr._config._batch_systems = _batch_systems
        awghr._config._general_cfg = _general_cfg

    try:
        _batch_systems = awghr._config._batch_systems
        awghr._config._batch_systems = [("dummy", "dummy", "true", None)]
        _general_cfg = awghr._config._general_cfg
        awghr._config._general_cfg = {"n_letters": None}
        src = """
[DEFAULT]
n_letters = abc
"""
        cfg = read_config(src)
        msg = "should have raised a ConfigException"
        raise AssertionError(msg)
    except ConfigException:
        pass
    finally:
        awghr._config._batch_systems = _batch_systems
        awghr._config._general_cfg = _general_cfg


@test
def parsing_dir():
    try:
        _batch_systems = awghr._config._batch_systems
        awghr._config._batch_systems = [("dummy", "dummy", "true", None)]
        _general_cfg = awghr._config._general_cfg
        awghr._config._general_cfg = {"srcdir": None, "destdir": Path.cwd()}
        src = f"""
[DEFAULT]
srcdir = {Path.cwd()}
"""
        cfg = read_config(src)
        assert cfg["srcdir"] == Path.cwd()
        assert cfg["destdir"] == Path.cwd()
    finally:
        awghr._config._batch_systems = _batch_systems
        awghr._config._general_cfg = _general_cfg

    try:
        _batch_systems = awghr._config._batch_systems
        awghr._config._batch_systems = [("dummy", "dummy", "true", None)]
        _general_cfg = awghr._config._general_cfg
        awghr._config._general_cfg = {"srcdir": None}
        src = """
[DEFAULT]
srcdir = /nonexistent/path1/../path2
"""
        cfg = read_config(src)
        assert cfg["srcdir"] == Path("/nonexistent") / "path2"
    finally:
        awghr._config._batch_systems = _batch_systems
        awghr._config._general_cfg = _general_cfg

    try:
        _batch_systems = awghr._config._batch_systems
        awghr._config._batch_systems = [("dummy", "dummy", "true", None)]
        _general_cfg = awghr._config._general_cfg
        awghr._config._general_cfg = {"srcdir": None}
        src = """
[DEFAULT]
srcdir = ~~
"""
        cfg = read_config(src)
        msg = "should have raised a ConfigException"
        raise AssertionError(msg)
    except ConfigException:
        pass
    finally:
        awghr._config._batch_systems = _batch_systems
        awghr._config._general_cfg = _general_cfg


@test
def parsing_list():
    try:
        _batch_systems = awghr._config._batch_systems
        awghr._config._batch_systems = [("dummy", "dummy", "true", None)]
        _general_cfg = awghr._config._general_cfg
        awghr._config._general_cfg = {"mylist": None}
        src = """
[DEFAULT]
mylist =
    a
"""
        cfg = read_config(src)
        assert cfg["mylist"] == ["a"]
        src = """
[DEFAULT]
mylist = a
[more items]
mylist =
    b
    c
"""
        cfg = read_config(src)
        assert cfg["mylist"] == ["a", "b", "c"]
        src = """
[DEFAULT]
mylist = a
[more items]
mylist = b
    c
[even more items]
mylist =
    e
    d
"""
        cfg = read_config(src)
        assert cfg["mylist"] == ["a", "b", "c", "e", "d"]
    finally:
        awghr._config._batch_systems = _batch_systems
        awghr._config._general_cfg = _general_cfg


@test
def parsing_host_target():
    try:
        awghr._config._override_host = "a1.b.c.d"
        for host, account in (
            ("a1.b.c.d", "localhost"),
            ("a", "localhost"),
            ("a1.", "localhost"),
            ("a1.b.", "localhost"),
            (".b.c.d", "localhost"),
            (".c.d", "localhost"),
            (".d", "localhost"),
            (".x.y", "nohost"),
            ("x.y.z", "nohost"),
            ("x", "nohost"),
        ):
            src = default_src
            src += f"""
[DEFAULT]
account = nohost

[parsing_host_target]
match host = {host}
account = localhost
"""
            cfg = read_config(src)
            assert cfg["account"] == account, (cfg["account"], account, host)
    finally:
        awghr._config._override_host = None


@test
def parsing_batch_target():
    try:
        _batch_systems = awghr._config._batch_systems
        awghr._config._batch_systems = [("dummy", "dummy", "true", None)]
        src = """
[DEFAULT]
account = acct

[parsing_batch_target]
match batch = dummy
account = anybatch
"""
        cfg = read_config(src)
        assert cfg["account"] == "anybatch"
    finally:
        awghr._config._batch_systems = _batch_systems

    try:
        src = default_src
        src += """
[DEFAULT]
account = acct

[parsing_batch_target_invalid_batch]
match batch = nonexistent
account = anybatch
"""
        cfg = read_config(src)
        msg = "should have raised a ConfigException"
        raise AssertionError(msg)
    except ConfigException:
        pass

    try:
        _batch_systems.append(("dummy", "dummy", "/nonexistent", None))
        src = default_src
        src += """
[DEFAULT]
account = acct

[parsing_batch_target_no_match]
match batch = dummy
account = anybatch
"""
        cfg = read_config(src)
        assert cfg["account"] == "acct"
    except ConfigException:
        pass
    finally:
        del _batch_systems[-1]


@test
def parsing_tag_target():
    src = default_src
    src += """
[DEFAULT]
account = acct

[parsing_tag_target]
match tag = sim
account = simacct
"""
    cfg = read_config(
        src,
        tags=["sim"],
    )
    assert cfg["account"] == "simacct"

    src = default_src
    src += """
[DEFAULT]
account = acct

[parsing_tag_target_no_tags]
match tag = notag
account = noacct
"""
    cfg = read_config(src)
    assert cfg["account"] == "acct"

    src = default_src
    src += """
[DEFAULT]
account = acct

[parsing_tag_target_no_matching_tag]
match tag = notag
account = noacct
"""
    cfg = read_config(src, tags=["tag"])
    assert cfg["account"] == "acct"


@test
def check_batch_system_keys():
    class DummyWorkflowWriter:
        KEYS = {"test": "def"}

    try:
        _batch_systems = awghr._config._batch_systems
        awghr._config._batch_systems = [("dummy", "dummy", "true", DummyWorkflowWriter)]
        src = """
[DEFAULT]
account = acct

[check_batch_system_keys]
match batch = dummy
test = abc
"""
        cfg = read_config(src)
        assert cfg["batch_system_cfg"]["test"] == "abc"
    finally:
        awghr._config._batch_systems = _batch_systems

    try:
        _batch_systems = awghr._config._batch_systems
        awghr._config._batch_systems = [("dummy", "dummy", "true", DummyWorkflowWriter)]
        src = """
[DEFAULT]
account = acct
"""
        cfg = read_config(src)
        assert cfg["batch_system_cfg"]["test"] == "def"
    finally:
        awghr._config._batch_systems = _batch_systems

    class DummyWorkflowWriter2:
        KEYS = {"test": None}

    try:
        _batch_systems = awghr._config._batch_systems
        awghr._config._batch_systems = [
            ("dummy", "dummy", "true", DummyWorkflowWriter2)
        ]
        src = """
[DEFAULT]
account = acct
"""
        cfg = read_config(src)
        msg = "should have raised a ConfigException"
        raise AssertionError(msg)
    except ConfigException:
        pass
    finally:
        awghr._config._batch_systems = _batch_systems


@test
def show():
    cli("config")
