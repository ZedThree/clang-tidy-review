FROM python:3

COPY requirements.txt /requirements.txt

RUN pip3 install --upgrade pip && \
    pip3 install -r requirements.txt

WORKDIR /action

COPY post.py /action/post.py
COPY clang_tidy_review /action/clang_tidy_review

ENTRYPOINT ["/action/post.py"]
