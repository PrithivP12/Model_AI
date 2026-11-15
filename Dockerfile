FROM python:3.14-slim
WORKDIR /app
COPY pyproject.toml requirements-release.lock ./
COPY agrosim ./agrosim
COPY fieldshift ./fieldshift
RUN pip install --no-cache-dir -r requirements-release.lock && pip install --no-cache-dir --no-deps .
RUN useradd --create-home agrosim && mkdir -p /data && chown agrosim /data
USER agrosim
ENV AGROSIM_DATA_DIR=/data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health',timeout=3)"
CMD ["python", "-m", "agrosim.api", "--host", "0.0.0.0", "--port", "8000"]
