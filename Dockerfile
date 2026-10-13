# syntax=docker/dockerfile:1
# HydrantProof — single-stage image.  The base is pinned by digest; this FROM
# line is the single source of the pin (bake_render_stack greps it — no
# duplicated string anywhere else).
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN useradd --system --no-create-home --shell /usr/sbin/nologin app

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Static assets (including the vendored Leaflet dist) are collected at build
# time; the runtime serves them through WhiteNoise with no build step.
RUN python manage.py collectstatic --noinput

# The media root exists in the image owned by `app` so that a freshly created
# named volume mounted at /app/media inherits app ownership (Docker copies the
# image directory's content + ownership into a new named volume).  The
# entrypoint additionally hands the tree to `app` when it starts as root,
# covering host mounts and pre-existing root-owned volumes.
RUN mkdir -p /app/media && chown app:app /app/media

# Freeze the render-stack facts the pack manifest cites (base image digest,
# python version, requirements hash, installed freeze hash).
RUN python manage.py bake_render_stack --output /app/render_stack.json

RUN chmod +x entrypoint.sh

# Persistent storage destination (attachments, overlays, stored pack bytes,
# stored import files).  No VOLUME declaration here: the persistence contract
# lives in runtime-services.json (storage hydrantproof_media at
# /data/hydrantproof_media), and the environment links the product's fixed
# path /app/media to it before the entrypoint runs.  An image VOLUME would
# create an anonymous volume that shadows that managed mount, so media bytes
# would not survive container replacement.  HYDRANTPROOF_MEDIA_ROOT overrides
# the root when a host sets one.

ENTRYPOINT ["./entrypoint.sh"]
