# syntax=docker/dockerfile:1

FROM ubuntu:22.04

LABEL name="Testing Slurm batch system" maintainer="Karl Wette"

WORKDIR /tmp

# Install prerequisites
ARG DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC
RUN <<EOF
apt-get update
apt-get -y upgrade
apt-get -y install \
    build-essential \
    bzip2 \
    less \
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
    libpam0g-dev \
    libperl-dev \
    libpmix-dev \
    librdkafka-dev \
    librrd-dev \
    libswitch-perl \
    libyaml-dev \
    python3.10 \
    python3.10-venv \
    ;
apt-get -y clean
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
tar xf slurm-${SLURM_VERSION}.tar.bz2
cd /tmp/slurm-${SLURM_VERSION}
./configure --prefix=/usr/local --with-systemdsystemunitdir=/lib/systemd/system
make all contrib
make install install-contrib
ldconfig /usr/local/lib
cd /tmp
rm -rf /tmp/*
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
NodeName=localhost Sockets=2 CoresPerSocket=1 RealMemory=500 TmpDisk=100 State=UNKNOWN
PartitionName=LocalQ Nodes=ALL Default=YES MaxTime=INFINITE State=UP
EOF
COPY <<EOF /usr/local/etc/cgroup.conf
CgroupPlugin=cgroup/v1
EOF

# Configure Slurm
RUN <<EOF
groupadd slurm
useradd -g slurm -d /nonexistent -s /usr/sbin/nologin slurm
chown slurm:slurm /usr/local/etc/slurm.conf /usr/local/etc/cgroup.conf
chmod 0400 /usr/local/etc/slurm.conf /usr/local/etc/cgroup.conf
mkdir -p /var/spool/slurmd /var/spool/slurmctld
chown slurm:slurm /var/spool/slurmd /var/spool/slurmctld
chmod 0700 /var/spool/slurmd /var/spool/slurmctld
EOF

# Install daemon-less systemctl
RUN <<EOF
apt-get -y remove systemd
apt-get -y install systemctl
apt-get -y clean
EOF

# Create entrypoint
COPY <<EOF /usr/local/bin/entrypoint
#!/bin/bash
set -e
if ! systemctl restart slurmctld slurmd; then
   echo "ERROR: could not start Slurm" >&2
   exit 1
fi
trap "systemctl stop slurmctld slurmd" 0 INT TERM
if ! scontrol update nodename=localhost state=idle; then
   echo "ERROR: could not run scontrol" >&2
   exit 1
fi
"\$@"
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
#SBATCH --output=slurm.out.%a
#SBATCH --error=slurm.err.%a
echo "slurm:\${SLURM_ARRAY_TASK_ID}"
EOF
RUN <<EOF
set -e
entrypoint sbatch --wait slurm.sh
for n in 0 1 2; do
  test "X`cat slurm.err.${n}`" = "X"
  test "X`cat slurm.out.${n}`" = "Xslurm:${n}"
done
rm -rf /tmp/*
EOF
