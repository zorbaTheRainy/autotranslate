# syntax=docker/dockerfile:1

# FROM python:3.7
FROM python:3.7-alpine

WORKDIR /app

# ---------------------------------
# ENVs supported by this Image
# ---------------------------------
# ENV DEEPL_AUTH_KEY=
# ENV DEEPL_TARGET_LANG=
# ENV CHECK_EVERY_X_MINUTES=
# ENV DEEPL_USAGE_RENEWAL_DAY=
ENV AM_I_IN_A_DOCKER_CONTAINER=1

COPY requirements.txt requirements.txt
COPY autotranslate.py autotranslate.py
COPY keep_alive.py keep_alive.py

# ---------------------------------
# What RUN does
# ---------------------------------
# 1. install requirements.txt
# 2. make the needed directories 
RUN pip3 install -r requirements.txt && \
    mkdir /inputDir /outputDir /logDir /tmpDir


ENTRYPOINT "./autotranslate.py"

# ---------------------------------
# Remember these commands 
# ---------------------------------
# pipreqs --force # to create requirements.txt ; cat requirements.txt
#
# docker login
# docker buildx build --platform=amd64,arm64 --push -t zorbatherainy/autotranslate -t zorbatherainy/autotranslate:2.1.1b .
# docker buildx build --platform=amd64,arm64 -t zorbatherainy/autotranslate  .
#
# docker build -t zorbatherainy/autotranslate .
# docker image ls
# docker tag z1234y123x12 zorbatherainy/autotranslate:2.1.1b
# docker rmi zorbatherainy/autotranslate:latest
# docker push zorbatherainy/autotranslate:2.1.1b
