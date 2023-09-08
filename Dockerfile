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
# 1a. install Node.js for `pip install translators` 
# 1b. install a bunch of development tools (gcc, etc.) for `pip install translators` because it wants to recompile cryptography # https://stackoverflow.com/questions/35736598/cannot-pip-install-cryptography-in-docker-alpine-linux-3-3-with-openssl-1-0-2g
# 2. install requirements.txt
# 3. uninstall all the development tools, as they are no longer needed and take up a lot of space
# 4. make the needed directories 

RUN apk add --no-cache gcc musl-dev python3-dev libffi-dev libressl-dev  nodejs npm && \
    pip3 install -r requirements.txt && \
    apk del gcc musl-dev python3-dev libffi-dev libressl-dev && \
    mkdir /inputDir /outputDir /logDir /tmpDir



ENTRYPOINT "./autotranslate.py"
