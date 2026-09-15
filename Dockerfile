FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY templates ./templates
COPY clusters.yaml .

# Non-root user for the container image. Named "tenantop", not "operator" --
# python:3.12-slim's base image already ships a system group called
# "operator" (GID 37, for telephony tooling), which collides with useradd's
# default of creating a same-named primary group.
RUN useradd -u 1000 -m tenantop && chown -R tenantop:tenantop /app
USER tenantop

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
