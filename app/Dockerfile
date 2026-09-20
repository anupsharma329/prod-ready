# ---- Stage 1: Build ----
# We use a full python image here ONLY to install/compile dependencies.
# This stage's layers are thrown away in the final image, so build tools
# (gcc, headers, etc.) never end up shipped to production.
FROM python:3.12-slim AS builder

WORKDIR /build

# Install into a local dir instead of system site-packages so we can
# cleanly COPY just the installed packages into the final stage.
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ---- Stage 2: Runtime ----
# This is the image that actually ships and runs in production.
# Notice: no compilers, no pip cache, no build tools -> smaller image,
# smaller attack surface, faster pulls on your K8s nodes.
FROM python:3.12-slim

# Security: create a dedicated non-root user. Running containers as root
# is one of the most common and most avoidable prod security findings.
# If an attacker breaks out of the app, root-in-container is much closer
# to root-on-host than a scoped-down user.
RUN groupadd -r appgroup && useradd -r -g appgroup -d /app -s /sbin/nologin appuser

WORKDIR /app

# Bring in the pre-built dependencies from the builder stage
COPY --from=builder /install /usr/local

COPY main.py .

# Ownership must be set explicitly - COPY defaults to root:root
RUN chown -R appuser:appgroup /app

USER appuser

# Document the port (informational only - does not actually publish it,
# that happens in K8s Service / docker-compose)
EXPOSE 8000

# Container-level health check. This is DIFFERENT from the K8s probes -
# this one is used if you run the container standalone (docker run) or
# via docker-compose, e.g. by `docker ps` or compose's depends_on:
#   condition: service_healthy
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Use exec form (JSON array) so uvicorn runs as PID 1 and correctly
# receives SIGTERM from Kubernetes/Docker for graceful shutdown.
# If you used the shell form ("CMD uvicorn ...") a shell process would be
# PID 1 instead, and signals wouldn't reach uvicorn properly -> pods would
# hang on termination until the grace period timeout, delaying rollouts.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]