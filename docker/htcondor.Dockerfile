# syntax=docker/dockerfile:1

FROM ubuntu:22.04

LABEL name="Testing HTCondor batch system" maintainer="Karl Wette"

WORKDIR /tmp

# Install prerequisites
ARG DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC
RUN <<EOF
apt-get update
apt-get -y upgrade
apt-get -y install \
    curl \
    less \
    ;
apt-get -y clean
EOF

# Download and install HTCondor
RUN <<EOF
curl -fsSL https://get.htcondor.org | /bin/bash -s -- --no-dry-run --channel stable
condor_off
apt-get -y clean
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
if ! systemctl restart condor; then
   echo "ERROR: could not start HTCondor" >&2
   exit 1
fi
trap "systemctl stop condor" 0 INT TERM
while ! pidof condor_collector >/dev/null; do
   sleep 0.5
done
"\$@"
EOF
RUN chmod 0755 /usr/local/bin/entrypoint
ENTRYPOINT ["/usr/local/bin/entrypoint"]
CMD ["/bin/bash"]

# Run a simple test
COPY <<EOF /tmp/condor.sub
universe = vanilla
executable = /usr/bin/echo
arguments = "condor:\$(Process)"
output = condor.out.\$(Process)
error = condor.err.\$(Process)
log = condor.log
queue 3
EOF
COPY <<EOF /tmp/condor.sh
set -e
condor_submit condor.sub
condor_wait condor.log
EOF
RUN <<EOF
entrypoint /bin/bash condor.sh
for n in 0 1 2; do
  test "X`cat condor.err.${n}`" = "X"
  test "X`cat condor.out.${n}`" = "Xcondor:${n}"
done
rm -rf /tmp/*
EOF
