# Python 3.12, not 3.13: uv.lock pins numpy 2.0.2, whose linux wheels stop at cp312.
# On 3.13 numpy would be built from source and fail without a toolchain.
FROM python:3.12-slim-bookworm AS builder

# Pinned to the uv that wrote this lock (version = 1). Never :latest - it would
# make the build non-reproducible.
COPY --from=ghcr.io/astral-sh/uv:0.5.5 /uv /bin/uv

# UV_PYTHON pins the interpreter even if .python-version (3.9) sneaks into the
# build context; UV_PYTHON_DOWNLOADS=never makes a mismatch fail loudly instead
# of silently provisioning a managed 3.9.
ENV UV_PYTHON=3.12 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app
COPY pyproject.toml uv.lock ./

# --locked so a drifted lock fails the build; --no-install-project because
# pyproject.toml has no [build-system] and the modules are loose top-level files.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev


# Same base image, so the venv's recorded interpreter path stays valid.
FROM python:3.12-slim-bookworm

# The venv is not relocatable - it must land at the identical path.
COPY --from=builder /app/.venv /app/.venv

# MPLCONFIGDIR: matplotlib builds a font cache on first import. Without a
# writable location a non-root user warns and rebuilds it on every single run.
# COMPILE_BYTECODE above precompiled the .pyc files; nothing writes any at runtime.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /app
COPY bkk_water_scraper.py bkk_water_discord.py bkk_water_ha.py ./
# --chmod=755: the host file is not executable, and a plain COPY would make the
# container die with "permission denied" at start, long after a green build.
COPY --chmod=755 docker-entrypoint.sh ./

RUN useradd --system --create-home appuser
USER appuser

ENTRYPOINT ["/app/docker-entrypoint.sh"]
