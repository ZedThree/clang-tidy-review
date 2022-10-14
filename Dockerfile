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
    pip3 install -r requirements.txt && \
    rm -rf /var/lib/apt/lists/

WORKDIR /action

COPY review.py /action/review.py

# Include the entirety of the post directory for simplicity's sake
# Technically we only need the clang_tidy_review directory but this keeps things consistent for running the command locally during development and in the docker image
COPY post /action/post

ENTRYPOINT ["/action/review.py"]
