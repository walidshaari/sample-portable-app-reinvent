# One image for every container target (EKS, kind, ECS). Built once, pushed
# once, and referenced by digest everywhere, so every target runs the same bytes.
#
# Base: Amazon Linux 2023 minimal, pinned by digest and passed in by the build
# script (codetalk_deployment/image/build-image.sh), which also runs the scan
# gate. No default on purpose: an unpinned base cannot slip in.
ARG BASE_IMAGE

FROM ${BASE_IMAGE} AS build
RUN microdnf install -y python3.11 python3.11-pip && microdnf clean all
COPY base_python/clean/requirements.txt /tmp/requirements.txt
RUN python3.11 -m pip install --no-cache-dir --no-compile --target /opt/deps -r /tmp/requirements.txt
COPY base_python/clean/ /app/
# __pycache__ and .pytest_cache are excluded by the repository .dockerignore.
RUN rm -rf /app/tests /app/requirements-dev.txt /app/pytest.ini \
 && python3.11 -m compileall -q /app /opt/deps

FROM ${BASE_IMAGE}
RUN microdnf install -y python3.11 && microdnf clean all && rm -rf /var/cache/dnf /var/lib/dnf/history*
COPY --from=build /opt/deps /opt/deps
COPY --from=build /app /app
WORKDIR /app
ENV PYTHONPATH=/app:/opt/deps \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=9000
EXPOSE 9000
# Numeric non-root user, so Kubernetes runAsNonRoot can verify it.
USER 10001:10001
CMD ["python3.11", "server.py"]
