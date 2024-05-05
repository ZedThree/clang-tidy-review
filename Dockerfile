FROM ubuntu:24.04

RUN apt update && \
    DEBIAN_FRONTEND=noninteractive \
    apt-get install -y --no-install-recommends\
    build-essential cmake git \
    tzdata \
    clang-tidy-14 \
    clang-tidy-15 \
    clang-tidy-16 \
    clang-tidy-17 \
    clang-tidy-18 \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/

COPY . /clang_tidy_review/

RUN python3 -m pip install --break-system-packages /clang_tidy_review/post/clang_tidy_review

ENTRYPOINT ["review"]
