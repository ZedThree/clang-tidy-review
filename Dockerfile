FROM ubuntu:22.04

RUN apt update && \
    DEBIAN_FRONTEND=noninteractive \
    apt-get install -y --no-install-recommends\
    build-essential cmake git \
    tzdata \
    clang-tidy-11 \
    clang-tidy-12 \
    clang-tidy-13 \
    clang-tidy-14 \
    clang-tidy-15 \
    clang-tidy-16 \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/

COPY . /clang_tidy_review/

RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install /clang_tidy_review/post/clang_tidy_review

ENTRYPOINT ["review"]
