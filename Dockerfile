FROM ubuntu:20.04

COPY requirements.txt /requirements.txt

RUN apt update && \
    DEBIAN_FRONTEND=noninteractive \
    apt-get install -y --no-install-recommends\
    build-essential cmake git \
    tzdata \
    clang-tidy-6.0 \
    clang-tidy-7 \
    clang-tidy-8 \
    clang-tidy-9 \
    clang-tidy-10 \
    clang-tidy-11 \
    clang-tidy-12 \
    python3 python3-pip && \
    pip3 install --upgrade pip && \
    pip3 install -r requirements.txt

COPY review.py /review.py

ENTRYPOINT ["/review.py"]
