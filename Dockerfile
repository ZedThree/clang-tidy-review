FROM ubuntu:22.04

COPY requirements.txt /requirements.txt

RUN apt update && \
    DEBIAN_FRONTEND=noninteractive \
    apt-get install -y --no-install-recommends\
    build-essential cmake git \
    tzdata \
    clang-tidy-11 \
    clang-tidy-12 \
    clang-tidy-13 \
    clang-tidy-14 \
    python3 python3-pip && \
    pip3 install --upgrade pip && \
    pip3 install -r requirements.txt

COPY review.py /review.py

ENTRYPOINT ["/review.py"]
