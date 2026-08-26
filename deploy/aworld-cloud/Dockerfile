FROM docker:27.5.1-cli AS docker-cli

FROM python:3.12.11-slim-bookworm

ARG HARBOR_COMMIT=ccf3df5ef50141b004322d4008b84b64797b76b9

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && git clone https://github.com/harbor-framework/harbor.git /opt/harbor \
    && git -C /opt/harbor checkout --detach "${HARBOR_COMMIT}" \
    && test "$(git -C /opt/harbor rev-parse HEAD)" = "${HARBOR_COMMIT}" \
    && python -m pip install --no-cache-dir /opt/harbor \
    && rm -rf /opt/harbor/.git

WORKDIR /app
COPY aworld/ /app/aworld/
COPY aworld_gateway/ /app/aworld_gateway/
COPY aworld-cli/src/aworld_cli/ /app/aworld-cli/src/aworld_cli/

ENV PYTHONPATH=/app:/app/aworld-cli/src \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "aworld.cloud.server:app", "--host", "0.0.0.0", "--port", "8000"]
