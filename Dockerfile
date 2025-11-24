ARG PYTHON_VERSION=3.14
ARG BUILD_TIME

FROM python:${PYTHON_VERSION}-alpine
# FROM python:${PYTHON_VERSION}

WORKDIR /app

# ---------------------------------
# ENVs supported by this Image
# ---------------------------------
# ENV DEEPL_AUTH_KEY=
# ENV DEEPL_TARGET_LANG=
# ENV CHECK_EVERY_X_MINUTES=
# ENV DEEPL_USAGE_RENEWAL_DAY=
# ENV ORIGINAL_BEFORE_TRANSLATION=
# ENV TRANSLATE_FILENAME=
# ENV NOTIFY_URLS=

COPY requirements.txt requirements.txt
COPY autotranslate.py autotranslate.py
COPY version.py version.py

RUN pip3 install -r requirements.txt && \
    mkdir /inputDir /outputDir /logDir

ENTRYPOINT "./autotranslate.py"
