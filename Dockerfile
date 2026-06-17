FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY pod1/requirements.txt pod1/requirements.txt
RUN pip install --no-cache-dir -r pod1/requirements.txt

# Copy source
COPY pod1/ pod1/
COPY shared/ shared/

# pod1/ modules import each other directly — add to PYTHONPATH
ENV PYTHONPATH=/app/pod1

# Streamlit config
ENV STREAMLIT_SERVER_PORT=8501
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0
ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

ENTRYPOINT ["streamlit", "run", "pod1/app.py"]
