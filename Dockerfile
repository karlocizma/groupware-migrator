FROM python:3.12-slim AS build
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

FROM python:3.12-slim
RUN useradd --create-home --shell /bin/bash app
WORKDIR /app
COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin/groupware-migrator /usr/local/bin/groupware-migrator
COPY --from=build /usr/local/bin/uvicorn /usr/local/bin/uvicorn
COPY src ./src
RUN mkdir -p /app/data && chown app:app /app/data
USER app
VOLUME ["/app/data"]
EXPOSE 8000
ENV PYTHONPATH=/app/src
CMD ["uvicorn", "groupware_migrator.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
