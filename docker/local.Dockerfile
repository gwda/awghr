# SPDX-FileCopyrightText: 2024 - 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

# syntax=docker/dockerfile:1

ARG UBUNTU_VERSION=26.04
ARG PYTHON_VERSION=3.14

FROM ubuntu:${UBUNTU_VERSION}
ARG PYTHON_VERSION
ENV PYTHON_VERSION=${PYTHON_VERSION}

LABEL name="Test awghr with local batch system" maintainer="Karl Wette"

WORKDIR /tmp

# Install prerequisites
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC
RUN --mount=type=cache,target=/var/lib/apt/lists,sharing=locked --mount=type=cache,target=/var/cache/apt,sharing=locked <<EOF
set -e
rm -f /etc/apt/apt.conf.d/docker-clean
apt-get update
apt-get -y upgrade
apt-get -y install \
    git \
    less \
    nano \
    python${PYTHON_VERSION} \
    python${PYTHON_VERSION}-venv \
    python3-pip \
    sudo \
    time \
    ;
EOF

# Configure test user
RUN <<EOF
set -e
mkdir -p /testing
groupadd testuser
useradd -g testuser -d /testing -s /bin/bash testuser
echo 'testuser ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers
EOF

# Create entrypoint
COPY <<'EOF' /usr/local/bin/entrypoint
#!/bin/bash
chown -R testuser:testuser /testing
chmod -R u+rwX,ug+rw /testing
su testuser -w PYTHON_VERSION -l -P -c "$*"
EOF
RUN chmod 0755 /usr/local/bin/entrypoint
ENTRYPOINT ["/usr/local/bin/entrypoint"]
CMD ["/bin/bash"]

# Configure test user environment
WORKDIR /testing
COPY <<'EOF' .bashrc
umask 002
export PS1='\\[\\e[1m\\]\\h:\\w\\[\\e[0m\\] $ '
alias ls='ls --color=always'
EOF
