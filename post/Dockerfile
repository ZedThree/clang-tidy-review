FROM python:3

COPY clang_tidy_review /clang_tidy_review

RUN pip3 install --upgrade pip && \
    pip3 install /clang_tidy_review

ENTRYPOINT ["post"]
