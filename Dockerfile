FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --create-home snoop
USER snoop
EXPOSE 8000
CMD ["uvicorn", "snoop_db.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
