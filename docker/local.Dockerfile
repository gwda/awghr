# SPDX-FileCopyrightText: 2024 - 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

# syntax=docker/dockerfile:1

FROM ubuntu:22.04

LABEL name="Testing local batch system" maintainer="Karl Wette"

WORKDIR /tmp

# Install prerequisites
ARG DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC
RUN --mount=type=cache,target=/var/lib/apt/lists,sharing=locked --mount=type=cache,target=/var/cache/apt,sharing=locked <<EOF
set -e
rm -f /etc/apt/apt.conf.d/docker-clean
apt-get update
apt-get -y upgrade
apt-get -y install \
    less \
    nano \
    python3-pip \
    python3.10 \
    python3.10-venv \
    ;
update-alternatives --install /usr/bin/python python /usr/bin/python3 1
update-alternatives --set python /usr/bin/python3
update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.10 1
update-alternatives --set python3 /usr/bin/python3.10
EOF

# Create entrypoint
COPY <<EOF /usr/local/bin/entrypoint
#!/bin/bash
exec "\$@"
EOF
RUN chmod 0755 /usr/local/bin/entrypoint
ENTRYPOINT ["/usr/local/bin/entrypoint"]
CMD ["/bin/bash"]

# Create testing directory
RUN <<EOF
mkdir -p /testing
chmod 0777 /testing
EOF
WORKDIR /testing
