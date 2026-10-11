# syntax=docker/dockerfile:1

# MarkETH media box (SPEC §9): ffmpeg behind a supervisor, and nothing else.
#
# The build context is this directory, which is also the Python package `mediabox`: it
# is copied to /app/mediabox and started as a module. Python's standard library and
# Alpine's ffmpeg are all the image holds; there is nothing to install with pip.
#
# The supervisor is process 1 of the container. compose.yaml sets no `init` for this
# service: the kernel delivers neither SIGKILL nor SIGSTOP to the first process of a
# container from inside it, so nothing the supervisor starts can end or freeze it.

FROM python:3.14-alpine
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
# The id 10001 is the owner of the two volumes in compose.yaml.
RUN apk add --no-cache ffmpeg \
    && addgroup -g 10001 media \
    && adduser -u 10001 -G media -D -H -s /sbin/nologin media
WORKDIR /app
COPY . mediabox/
USER media
CMD ["python", "-m", "mediabox"]
