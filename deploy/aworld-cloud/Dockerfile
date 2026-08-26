FROM docker:27.5.1-cli AS docker-cli

FROM python:3.12.11-slim-bookworm

ARG HARBOR_COMMIT=ccf3df5ef50141b004322d4008b84b64797b76b9
ARG HARBOR_ARCHIVE_SHA256=4c17f78744952ecafd09951d85e0065bc5fa1660aff591ffad2ee42ffee5e8c8

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose

ADD https://github.com/harbor-framework/harbor/archive/${HARBOR_COMMIT}.tar.gz /tmp/harbor.tar.gz
RUN echo "${HARBOR_ARCHIVE_SHA256}  /tmp/harbor.tar.gz" | sha256sum --check --strict - \
    && python -m pip install --no-cache-dir /tmp/harbor.tar.gz \
    && rm /tmp/harbor.tar.gz

WORKDIR /app
COPY aworld/ /app/aworld/
COPY aworld_gateway/ /app/aworld_gateway/
COPY aworld-cli/src/aworld_cli/ /app/aworld-cli/src/aworld_cli/

ENV PYTHONPATH=/app:/app/aworld-cli/src \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "aworld.cloud.server:app", "--host", "0.0.0.0", "--port", "8000"]
