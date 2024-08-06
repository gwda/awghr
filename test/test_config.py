from pathlib import Path

import awghr.config
from awghr.config import ConfigException, _batch_systems, read_config, show_config

default_src = """
[DEFAULT SLURM]
match batch = slurm
jobfs = /tmp
[DEFAULT PBS]
match batch = pbs
jobfs = /tmp
"""


def test(f):
    print(f"----- {__file__}: unit test {f.__name__}", flush=True)
    f()


@test
def multiple_batch_system():
    try:
        _batch_systems.append(("dummy", "bash", None))
        read_config("")
        raise AssertionError("should have raised a ConfigException")
    except ConfigException:
        pass
    finally:
        del _batch_systems[-1]


@test
def no_batch_system():
    try:
        _batch_systems = awghr.config._batch_systems
        awghr.config._batch_systems = [("dummy", "nonexistent", None)]
        read_config("")
        raise AssertionError("should have raised a ConfigException")
    except ConfigException:
        pass
    finally:
        awghr.config._batch_systems = _batch_systems


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
        raise AssertionError("should have raised a ConfigException")
    except ConfigException:
        pass


@test
def invalid_section_key_type():
    src = default_src
    src += """
[DEFAULT]
account = acct
dated? = why
"""
    try:
        read_config(src)
        raise AssertionError("should have raised a ConfigException")
    except ConfigException:
        pass


@test
def missing_required_key():
    src = default_src
    src += """
[DEFAULT]
# no account
"""
    try:
        read_config(src)
        raise AssertionError("should have raised a ConfigException")
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
        raise AssertionError("should have raised a ConfigException")
    except ConfigException:
        pass


@test
def parsing_bool():
    try:
        _batch_systems = awghr.config._batch_systems
        awghr.config._batch_systems = [("dummy", "true", None)]
        _keys = awghr.config._keys
        awghr.config._keys = {"why?": None, "why not?": None, "could be?": True}
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
        awghr.config._batch_systems = _batch_systems
        awghr.config._keys = _keys

    try:
        _batch_systems = awghr.config._batch_systems
        awghr.config._batch_systems = [("dummy", "true", None)]
        _keys = awghr.config._keys
        awghr.config._keys = {"why?": None}
        src = """
[DEFAULT]
why? = because
"""
        cfg = read_config(src)
        raise AssertionError("should have raised a ConfigException")
    except ConfigException:
        pass
    finally:
        awghr.config._batch_systems = _batch_systems
        awghr.config._keys = _keys


@test
def parsing_int():
    try:
        _batch_systems = awghr.config._batch_systems
        awghr.config._batch_systems = [("dummy", "true", None)]
        _keys = awghr.config._keys
        awghr.config._keys = {"n_letters": None, "n_words": None, "n_pages": 3}
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
        awghr.config._batch_systems = _batch_systems
        awghr.config._keys = _keys

    try:
        _batch_systems = awghr.config._batch_systems
        awghr.config._batch_systems = [("dummy", "true", None)]
        _keys = awghr.config._keys
        awghr.config._keys = {"n_letters": None}
        src = """
[DEFAULT]
n_letters = abc
"""
        cfg = read_config(src)
        raise AssertionError("should have raised a ConfigException")
    except ConfigException:
        pass
    finally:
        awghr.config._batch_systems = _batch_systems
        awghr.config._keys = _keys


@test
def parsing_dir():
    try:
        _batch_systems = awghr.config._batch_systems
        awghr.config._batch_systems = [("dummy", "true", None)]
        _keys = awghr.config._keys
        awghr.config._keys = {"srcdir": None, "destdir": Path.cwd()}
        src = f"""
[DEFAULT]
srcdir = {Path.cwd()}
"""
        cfg = read_config(src)
        assert cfg["srcdir"] == Path.cwd()
        assert cfg["destdir"] == Path.cwd()
    finally:
        awghr.config._batch_systems = _batch_systems
        awghr.config._keys = _keys

    try:
        _batch_systems = awghr.config._batch_systems
        awghr.config._batch_systems = [("dummy", "true", None)]
        _keys = awghr.config._keys
        awghr.config._keys = {"srcdir": None}
        src = """
[DEFAULT]
srcdir = /nonexistent
"""
        cfg = read_config(src)
        raise AssertionError("should have raised a ConfigException")
    except ConfigException:
        pass
    finally:
        awghr.config._batch_systems = _batch_systems
        awghr.config._keys = _keys


@test
def parsing_list():
    try:
        _batch_systems = awghr.config._batch_systems
        awghr.config._batch_systems = [("dummy", "true", None)]
        _keys = awghr.config._keys
        awghr.config._keys = {"mylist": None}
        src = """
[DEFAULT]
mylist = a
"""
        cfg = read_config(src)
        assert cfg["mylist"] == ["a"]
        src = """
[DEFAULT]
mylist = a
[more items]
mylist = b c
"""
        cfg = read_config(src)
        assert cfg["mylist"] == ["a", "b", "c"]
        src = """
[DEFAULT]
mylist = a
[more items]
mylist = b c
[even more items]
mylist = e d
"""
        cfg = read_config(src)
        assert cfg["mylist"] == ["a", "b", "c", "e", "d"]
    finally:
        awghr.config._batch_systems = _batch_systems
        awghr.config._keys = _keys


@test
def parsing_host_target():
    try:
        awghr.config._override_host = "a1.b.c.d"
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
        awghr.config._override_host = None


@test
def parsing_batch_target():
    try:
        _batch_systems = awghr.config._batch_systems
        awghr.config._batch_systems = [("dummy", "true", None)]
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
        awghr.config._batch_systems = _batch_systems

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
        raise AssertionError("should have raised a ConfigException")
    except ConfigException:
        pass

    try:
        _batch_systems.append(("dummy", "/nonexistent", None))
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
        _batch_systems = awghr.config._batch_systems
        awghr.config._batch_systems = [("dummy", "true", DummyWorkflowWriter)]
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
        awghr.config._batch_systems = _batch_systems

    try:
        _batch_systems = awghr.config._batch_systems
        awghr.config._batch_systems = [("dummy", "true", DummyWorkflowWriter)]
        src = """
[DEFAULT]
account = acct
"""
        cfg = read_config(src)
        assert cfg["batch_system_cfg"]["test"] == "def"
    finally:
        awghr.config._batch_systems = _batch_systems

    class DummyWorkflowWriter2:
        KEYS = {"test": None}

    try:
        _batch_systems = awghr.config._batch_systems
        awghr.config._batch_systems = [("dummy", "true", DummyWorkflowWriter2)]
        src = """
[DEFAULT]
account = acct
"""
        cfg = read_config(src)
        raise AssertionError("should have raised a ConfigException")
    except ConfigException:
        pass
    finally:
        awghr.config._batch_systems = _batch_systems


@test
def show():
    show_config()
