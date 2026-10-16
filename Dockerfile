FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config.yaml pyproject.toml README.md ./
COPY src ./src
COPY .streamlit ./.streamlit

RUN mkdir -p /app/data /app/output

EXPOSE 8501

# The dashboard reads the latest SQLite snapshot. Override the command to scan:
#   python -m src.main --universe sample --min-mos 0.25
CMD ["streamlit", "run", "src/dashboard/app.py", "--server.address=0.0.0.0", "--server.port=8501"]
