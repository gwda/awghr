# SPDX-FileCopyrightText: 2024 - 2025 Karl Wette
#
# SPDX-License-Identifier: MIT

# syntax=docker/dockerfile:1

FROM ubuntu:22.04 AS build

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
    build-essential \
    bzip2 \
    libcurl4-openssl-dev \
    libfreeipmi-dev \
    libgtk2.0-dev \
    libhdf5-dev \
    libhttp-parser-dev \
    libhwloc-dev \
    libipmimonitoring-dev \
    libjson-c-dev \
    libjwt-dev \
    liblua5.3-dev \
    liblz4-dev \
    libmysqlclient-dev \
    libncurses-dev \
    libpam0g-dev \
    libperl-dev \
    libpmix-dev \
    librdkafka-dev \
    librrd-dev \
    libswitch-perl \
    libyaml-dev \
    python3.10 \
    ;
update-alternatives --install /usr/bin/python python /usr/bin/python3 1
update-alternatives --set python /usr/bin/python3
update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.10 1
update-alternatives --set python3 /usr/bin/python3.10
EOF

# Download Slurm
ARG SLURM_VERSION=23.02.6
ADD https://download.schedmd.com/slurm/slurm-${SLURM_VERSION}.tar.bz2 /tmp

# Build and install Slurm
RUN <<EOF
set -e
tar xf slurm-${SLURM_VERSION}.tar.bz2
cd /tmp/slurm-${SLURM_VERSION}
./configure --prefix=/usr/local --with-systemdsystemunitdir=/lib/systemd/system
make all contrib
make install install-contrib DESTDIR=/tmp/slurm-install
EOF

FROM ubuntu:22.04

LABEL name="Testing Slurm batch system" maintainer="Karl Wette"

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
    less \
    libcurl4 \
    libfreeipmi17 \
    libgtk2.0-0 \
    libhdf5-103 \
    libhttp-parser2.9 \
    libhwloc15 \
    libipmimonitoring6 \
    libjson-c5 \
    libjwt0 \
    liblua5.3-0 \
    liblz4-1 \
    libmysqlclient21 \
    libncurses6 \
    libpam0g \
    libpmix-dev \
    librdkafka1 \
    librrd8 \
    libswitch-perl \
    libyaml-0-2 \
    nano \
    perl \
    python3-pip \
    python3.10 \
    python3.10-venv \
    systemctl \
    ;
update-alternatives --install /usr/bin/python python /usr/bin/python3 1
update-alternatives --set python /usr/bin/python3
update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.10 1
update-alternatives --set python3 /usr/bin/python3.10
EOF

# Copy installed Slurm from build image
COPY --from=build /tmp/slurm-install/usr/ /usr/
COPY --from=build /tmp/slurm-install/lib/ /lib/
RUN <<EOF
set -e
ldconfig /usr/local/lib
EOF

# Create Slurm configuration
COPY <<EOF /usr/local/etc/slurm.conf
ClusterName=localcluster
SlurmctldHost=localhost
MpiDefault=none
ProctrackType=proctrack/linuxproc
ReturnToService=2
SlurmctldPidFile=/var/run/slurmctld.pid
SlurmctldPort=6817
SlurmdPidFile=/var/run/slurmd.pid
SlurmdPort=6818
SlurmdSpoolDir=/var/spool/slurmd
SlurmUser=slurm
StateSaveLocation=/var/spool/slurmctld
SwitchType=switch/none
TaskPlugin=task/none
InactiveLimit=0
KillWait=30
MinJobAge=300
SlurmctldTimeout=120
SlurmdTimeout=300
Waittime=0
SchedulerType=sched/backfill
SelectType=select/cons_tres
SelectTypeParameters=CR_Core
AccountingStorageType=accounting_storage/none
JobCompType=jobcomp/none
JobAcctGatherFrequency=30
JobAcctGatherType=jobacct_gather/none
SlurmctldDebug=info
SlurmctldLogFile=/var/log/slurmctld.log
SlurmdDebug=info
SlurmdLogFile=/var/log/slurmd.log
AuthType=auth/none
CredType=cred/none
JobContainerType=job_container/none
MaxArraySize=10
NodeName=localhost Sockets=2 CoresPerSocket=1 RealMemory=500 TmpDisk=100 State=UNKNOWN
PartitionName=LocalQ Nodes=ALL Default=YES MaxTime=INFINITE State=UP
EOF
COPY <<EOF /usr/local/etc/cgroup.conf
CgroupPlugin=cgroup/v1
EOF

# Configure Slurm
RUN <<EOF
set -e
groupadd slurm
useradd -g slurm -d /nonexistent -s /usr/sbin/nologin slurm
chown slurm:slurm /usr/local/etc/slurm.conf /usr/local/etc/cgroup.conf
chmod 0444 /usr/local/etc/slurm.conf /usr/local/etc/cgroup.conf
mkdir -p /var/spool/slurmd /var/spool/slurmctld
chown slurm:slurm /var/spool/slurmd /var/spool/slurmctld
chmod 0755 /var/spool/slurmd
chmod 0700 /var/spool/slurmctld
EOF

# Create entrypoint
COPY <<EOF /usr/local/bin/entrypoint
#!/bin/bash
set -e
if ! systemctl restart slurmctld slurmd; then
    echo "ERROR: could not start Slurm" >&2
    exit 1
fi
scancel --me --full --hurry
scontrol update nodename=localhost state=down reason=shutdown
_shutdown_slurm() {
    status=\$?
    scancel --me --full --hurry
    scontrol update nodename=localhost state=down reason=shutdown
    systemctl stop slurmctld slurmd
    exit \${status}
}
trap _shutdown_slurm EXIT INT TERM
if ! scontrol update nodename=localhost state=idle; then
    echo "ERROR: could not run scontrol" >&2
    exit 1
fi
chown -R testuser:testuser /testing
chmod -R u+rwX,ug+rw /testing
su testuser -l -P -c "\$*"
EOF
RUN chmod 0755 /usr/local/bin/entrypoint
ENTRYPOINT ["/usr/local/bin/entrypoint"]
CMD ["/bin/bash"]

# Run a simple test
COPY <<EOF /tmp/slurm.sh
#!/bin/bash
#SBATCH --array=0-2
#SBATCH --time=10:00
#SBATCH --mem=10MB
#SBATCH --tmp=10MB
#SBATCH --output=/tmp/slurm.out.%a
#SBATCH --error=/tmp/slurm.err.%a
echo "slurm:\${SLURM_ARRAY_TASK_ID}"
EOF
RUN <<EOF
set -e
entrypoint sbatch --wait /tmp/slurm.sh
for n in 0 1 2; do
    test "X`cat slurm.err.${n}`" = "X"
    test "X`cat slurm.out.${n}`" = "Xslurm:${n}"
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
