FROM ubuntu:22.04

# Prevent apt from opening interactive dialogs during build.
ENV DEBIAN_FRONTEND=noninteractive

# ---------------------------------------------------------------
# Install OpenSSH and the tools needed for Computer Systems labs.
# Keep the image minimal — no package manager abuse, no extras.
# ---------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    # SSH server — the only exposed service
    openssh-server \
    # Shell and editors
    bash \
    vim \
    nano \
    # Networking tools (used in lab exercises)
    net-tools \
    iproute2 \
    iputils-ping \
    dnsutils \
    curl \
    wget \
    # Process inspection
    procps \
    # Manual pages
    man-db \
    manpages \
    # Misc utilities
    unzip \
    less \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------
# Create the lab user. Root login via SSH is disabled.
# The password is intentionally simple — authentication is the
# token URL, not the SSH credential. The SSH session is only
# reachable via guacd; it is never exposed to the internet.
# ---------------------------------------------------------------
RUN useradd -m -s /bin/bash labuser \
    && echo 'labuser:labpass' | chpasswd

# ---------------------------------------------------------------
# SSH hardening
# - Root login disabled
# - Password authentication enabled (required for guacd/SSH proxy)
# - Only labuser is allowed to log in
# - PrintMotd off to keep the terminal clean on connect
# ---------------------------------------------------------------
RUN mkdir /var/run/sshd \
    && sed -i 's/#PasswordAuthentication yes/PasswordAuthentication yes/' \
        /etc/ssh/sshd_config \
    && sed -i 's/PermitRootLogin prohibit-password/PermitRootLogin no/' \
        /etc/ssh/sshd_config \
    && echo "AllowUsers labuser"    >> /etc/ssh/sshd_config \
    && echo "PrintMotd no"          >> /etc/ssh/sshd_config \
    && echo "X11Forwarding no"      >> /etc/ssh/sshd_config \
    && echo "MaxAuthTries 3"        >> /etc/ssh/sshd_config

# SSH is the only open port. guacd reaches this container over
# the lab-students bridge — no public exposure.
EXPOSE 22

CMD ["/usr/sbin/sshd", "-D", "-e"]
