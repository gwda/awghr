# SPDX-FileCopyrightText: 2024 - 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

# syntax=docker/dockerfile:1

FROM ubuntu:22.04

LABEL name="Testing HTCondor batch system" maintainer="Karl Wette"

WORKDIR /tmp

# Configure test user
RUN <<EOF
set -e
mkdir -p /testing
groupadd testuser
useradd -g testuser -d /testing -s /bin/bash testuser
EOF

# Install prerequisites
ARG DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC
RUN --mount=type=cache,target=/var/lib/apt/lists,sharing=locked --mount=type=cache,target=/var/cache/apt,sharing=locked <<EOF
set -e
rm -f /etc/apt/apt.conf.d/docker-clean
apt-get update
apt-get -y upgrade
apt-get -y install \
    curl \
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

# Download and install HTCondor
RUN --mount=type=cache,target=/var/lib/apt/lists,sharing=locked --mount=type=cache,target=/var/cache/apt,sharing=locked <<EOF
set -e
curl -fsSL https://get.htcondor.org | sed 's/CHANNEL_DIR=[0-9][0-9.]*$/CHANNEL_DIR=23.0/' | /bin/bash -s -- --no-dry-run --channel stable
condor_off
EOF

# Install daemon-less systemctl
RUN --mount=type=cache,target=/var/lib/apt/lists,sharing=locked --mount=type=cache,target=/var/cache/apt,sharing=locked <<EOF
set -e
apt-get -y remove systemd
apt-get -y install systemctl
EOF

# Create additional HTCondor configuration
COPY <<EOF /etc/condor/config.d/01-local
NUM_SLOTS = 2
EOF

# Create entrypoint
COPY <<EOF /usr/local/bin/entrypoint
#!/bin/bash
set -e
if ! systemctl restart condor; then
    echo "ERROR: could not start HTCondor" >&2
    exit 1
fi
trap 'status=\$?; systemctl stop condor; exit \${status}' EXIT INT TERM
while ! pidof condor_collector >/dev/null; do
    sleep 0.5
done
chown -R testuser:testuser /testing
chmod -R u+rwX,ug+rw /testing
su testuser -l -P -c "\$*"
EOF
RUN chmod 0755 /usr/local/bin/entrypoint
ENTRYPOINT ["/usr/local/bin/entrypoint"]
CMD ["/bin/bash"]

# Run a simple test
COPY <<EOF /tmp/condor.sub
universe = vanilla
initialdir = /tmp
executable = /usr/bin/echo
arguments = "condor:\$(Process)"
output = condor.out.\$(Process)
error = condor.err.\$(Process)
log = condor.log
queue 3
EOF
COPY <<EOF /tmp/condor.sh
set -e
condor_submit /tmp/condor.sub
condor_wait /tmp/condor.log
EOF
RUN <<EOF
set -e
entrypoint /bin/bash /tmp/condor.sh
for n in 0 1 2; do
    test "X`cat condor.err.${n}`" = "X"
    test "X`cat condor.out.${n}`" = "Xcondor:${n}"
done
rm -rf /tmp/*
EOF

# Configure test user environment
WORKDIR /testing
COPY <<EOF .bashrc
umask 002
export PS1='\\[\\e[1m\\]\\h:\\w\\[\\e[0m\\] \$ '
alias ls='ls --color=always'
EOF
