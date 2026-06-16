FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    # SSH server — the only exposed service
    openssh-server \
    # Shell and editors
    bash \
    vim \
    nano \
    net-tools \
    iproute2 \
    iputils-ping \
    dnsutils \
    curl \
    wget \
    procps \
    # Manual pages
    man-db \
    manpages \
    unzip \
    less \
    && rm -rf /var/lib/apt/lists/*


RUN useradd -m -s /bin/bash labuser \
    && echo 'labuser:labpass' | chpasswd


RUN mkdir /var/run/sshd \
    && sed -i 's/#PasswordAuthentication yes/PasswordAuthentication yes/' \
        /etc/ssh/sshd_config \
    && sed -i 's/PermitRootLogin prohibit-password/PermitRootLogin no/' \
        /etc/ssh/sshd_config \
    && echo "AllowUsers labuser"    >> /etc/ssh/sshd_config \
    && echo "PrintMotd no"          >> /etc/ssh/sshd_config \
    && echo "X11Forwarding no"      >> /etc/ssh/sshd_config \
    && echo "MaxAuthTries 3"        >> /etc/ssh/sshd_config
    
EXPOSE 22

CMD ["/usr/sbin/sshd", "-D", "-e"]
