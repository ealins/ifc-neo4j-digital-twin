FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-fallback.txt ./
RUN pip install -r requirements-fallback.txt
# IfcOpenShell is the preferred standards-aware parser. The repository also
# includes a built-in STEP fallback so the image remains usable on platforms
# where a compatible wheel is temporarily unavailable.
RUN pip install "ifcopenshell>=0.8.0,<0.9" || echo "IfcOpenShell wheel unavailable; STEP fallback will be used"

COPY pyproject.toml README.md ./
COPY src ./src
COPY viewer ./viewer
RUN pip install --no-deps -e .

ENV IFC_DATA_DIR=/data VIEWER_DIR=/app/viewer
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "ifc_graph.api:app", "--host", "0.0.0.0", "--port", "8000"]
