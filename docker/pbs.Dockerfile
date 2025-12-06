# SPDX-FileCopyrightText: 2024 - 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

# syntax=docker/dockerfile:1

FROM ubuntu:22.04 AS build

RUN grep -q '[[:space:]]pbs-ubuntu-host$' /etc/hosts

WORKDIR /tmp

# Install build prerequisites
ARG DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC
RUN --mount=type=cache,target=/var/lib/apt/lists,sharing=locked --mount=type=cache,target=/var/cache/apt,sharing=locked <<EOF
set -e
rm -f /etc/apt/apt.conf.d/docker-clean
apt-get update
apt-get -y upgrade
apt-get -y install \
    autoconf \
    automake \
    build-essential \
    gzip \
    libedit-dev \
    libexpat-dev \
    libhwloc-dev \
    libical-dev \
    libssl-dev \
    libtool \
    libx11-dev \
    libxext-dev \
    libxft-dev \
    libxt-dev \
    ncurses-dev perl \
    pkg-config \
    postgresql-contrib \
    postgresql-server-dev-all \
    python3-dev \
    python3-pip \
    python3.10 \
    python3.10-venv \
    swig \
    tcl-dev \
    tk-dev \
    ;
update-alternatives --install /usr/bin/python python /usr/bin/python3 1
update-alternatives --set python /usr/bin/python3
update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.10 1
update-alternatives --set python3 /usr/bin/python3.10
update-alternatives --install /usr/bin/python-config python-config /usr/bin/python3.10-config 1
update-alternatives --set python-config /usr/bin/python3.10-config
update-alternatives --install /usr/bin/python3-config python3-config /usr/bin/python3.10-config 1
update-alternatives --set python3-config /usr/bin/python3.10-config
echo dash dash/sh boolean false | debconf-set-selections
dpkg-reconfigure -f noninteractive dash
EOF

# Download OpenPBS
ARG OPENPBS_VERSION=22.05.11
ADD https://github.com/openpbs/openpbs/archive/refs/tags/v${OPENPBS_VERSION}.tar.gz /tmp

# Build and install OpenPBS
RUN <<EOF
set -e
tar xf v${OPENPBS_VERSION}.tar.gz
cd /tmp/openpbs-${OPENPBS_VERSION}
./autogen.sh
./configure --prefix=/usr/local
make
make install DESTDIR=/tmp/openpbs-install
rm -f /tmp/openpbs-install/usr/local/lib/init.d/limits.*
EOF

FROM ubuntu:22.04

LABEL name="Testing PBS batch system" maintainer="Karl Wette"

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
    expat \
    less \
    libedit2 \
    libhwloc15 \
    libical3 \
    nano \
    postgresql \
    postgresql-contrib \
    python3-pip \
    python3.10 \
    python3.10-venv \
    systemctl \
    tcl \
    time \
    tk \
    ;
update-alternatives --install /usr/bin/python python /usr/bin/python3 1
update-alternatives --set python /usr/bin/python3
update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.10 1
update-alternatives --set python3 /usr/bin/python3.10
echo dash dash/sh boolean false | debconf-set-selections
dpkg-reconfigure -f noninteractive dash
EOF

# Copy installed PBS from build image
COPY --from=build /tmp/openpbs-install/usr/ /usr/
COPY --from=build /tmp/openpbs-install/lib/ /lib/
COPY --from=build /tmp/openpbs-install/etc/ /etc/
RUN <<EOF
set -e
ldconfig /usr/local/lib
/usr/local/libexec/pbs_postinstall
EOF

# Create OpenPBS configuration
COPY <<EOF /etc/pbs.conf
PBS_SERVER=pbs-ubuntu-host
PBS_START_SERVER=1
PBS_START_SCHED=1
PBS_START_COMM=1
PBS_START_MOM=1
PBS_EXEC=/usr/local
PBS_HOME=/var/spool/pbs
PBS_CORE_LIMIT=unlimited
EOF
COPY <<EOF /var/spool/pbs/mom_priv/config
\$clienthost pbs-ubuntu-host
EOF

# Configure OpenPBS
RUN --network=host <<EOF
set -e
chmod 4755 /usr/local/sbin/pbs_iff /usr/local/sbin/pbs_rcp
/etc/init.d/pbs start || true
qmgr -c "set server flatuid=true,max_array_size=10"
qmgr -c "set node pbs-ubuntu-host resources_available.ncpus=2,resources_available.mem=100Mb"
/etc/init.d/pbs stop
EOF

# Create entrypoint
COPY <<EOF /usr/local/bin/entrypoint
#!/bin/bash
set -e
if ! grep -q '[[:space:]]pbs-ubuntu-host$' /etc/hosts; then
    echo "ERROR: hostname needs to be 'pbs-ubuntu-host'" >&2
    exit 1
fi
if ! systemctl restart pbs; then
    echo "ERROR: could not start OpenPBS" >&2
    exit 1
fi
while true; do
    sleep 0.2
    nodeparams=`( pbsnodes -F dsv pbs-ubuntu-host || true ) 2>/dev/null`
    case "\${nodeparams}"  in
        *"|state=free|"*)
            break
            ;;
        *)
            if ! systemctl status pbs >/dev/null 2>&1; then
                echo "ERROR: OpenPBS service has died" >&2
                exit 1
            fi
            ;;
    esac
done
chown -R testuser:testuser /testing
chmod -R u+rwX,ug+rw /testing
su testuser -l -P -c "\$*"
EOF
RUN chmod 0755 /usr/local/bin/entrypoint
ENTRYPOINT ["/usr/local/bin/entrypoint"]
CMD ["/bin/bash"]

# Run a simple test
COPY <<EOF /tmp/pbs.sh
#!/bin/bash
#PBS -J 0-2
#PBS -l walltime=10:00
#PBS -l mem=10MB
#PBS -o /tmp/pbs.out.^array_index^
#PBS -e /tmp/pbs.err.^array_index^
echo "pbs:\${PBS_ARRAY_INDEX}"
EOF
RUN --network=host <<EOF
set -e
entrypoint qsub -W block=true /tmp/pbs.sh
for n in 0 1 2; do
    test "X`cat pbs.err.${n}`" = "X"
    test "X`cat pbs.out.${n}`" = "Xpbs:${n}"
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
