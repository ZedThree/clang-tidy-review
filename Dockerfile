FROM ubuntu:mantic

ARG DEBIAN_FRONTEND=noninteractive

RUN apt update -y && \
    apt upgrade -y && \
    apt install -y --no-install-recommends \
    build-essential \
    git \
    ca-certificates \
    gpg \
    wget && \
    wget -O - https://apt.kitware.com/keys/kitware-archive-latest.asc 2>/dev/null | gpg --dearmor - | tee /usr/share/keyrings/kitware-archive-keyring.gpg >/dev/null && \
    echo 'deb [signed-by=/usr/share/keyrings/kitware-archive-keyring.gpg] https://apt.kitware.com/ubuntu/ jammy main' > /etc/apt/sources.list.d/kitware.list && \
    echo 'deb http://apt.llvm.org/mantic/ llvm-toolchain-mantic-17 main' >> /etc/apt/sources.list && \
    echo 'deb-src http://apt.llvm.org/mantic/ llvm-toolchain-mantic-17 main' >> /etc/apt/sources.list && \
    wget -qO- https://apt.llvm.org/llvm-snapshot.gpg.key | tee /etc/apt/trusted.gpg.d/apt.llvm.org.asc && \
    apt update -y && \
    rm /usr/share/keyrings/kitware-archive-keyring.gpg && \
    apt install -y --no-install-recommends \
    kitware-archive-keyring \
    cmake \
    tzdata \
    clang-tidy-13 \
    clang-tidy-14 \
    clang-tidy-15 \
    clang-tidy-16 \
    clang-tidy-17 \
    python3 \
    python3-pip && \
    rm -rf /var/lib/apt/lists/*

COPY . /clang_tidy_review/

RUN python3 -m pip install --break-system-packages /clang_tidy_review/post/clang_tidy_review

ENTRYPOINT ["review"]
