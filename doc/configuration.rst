Configuration
=============

Default workflow configurations are retrieved from ``~/.awghr.cfg``.

This is a ``ConfigParser``-style configuration file with the following syntax::

    [DEFAULT]           # This section match all batch systems/hosts/tags
    account = ...           # Default value of ``account`` argument passed to Workflow()
    basedir = ...           # Default value of ``basedir`` argument passed to Workflow()
    dated? = ...            # Default value of ``dated`` argument passed to Workflow()
    envlist =               # List of environment variables to save/set in job script:
        *PATH               # - Save any variables matching this regular expression.
        VAR=val             # - Set variable to the given value.
        ...                 #   May contain ``{format}`` strings which will be filled by:
                            #   * ``_time_min``: requested wall time in minutes.
                            #   * ``_mem_MB``: requested memory in MB.
                            #   * ``_ncpus``: requested number of CPUs.
                            #   * ``_ngpus``: requested number of GPUs.

    # Section headings are arbitrary
    [host matching]     # This section matches the host ``login1.example.com``
    match host = login1.example.com
    match host = .example.com
    match host = login1
    match host = login
    cluster = cluster1
    ...

    [batch matching]    # This section matches a given batch system
    match batch = ...
    # Batch system matching sections may include system-specific keys

    [tag matching]      # This section matches a tag in the ``tags`` list passed to Workflow()
    match tag = ...
    ...
