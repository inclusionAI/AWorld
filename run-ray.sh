#!/bin/bash
cd "$(dirname "$0")"

docker compose -f docker-compose-ray.yml build && \
  docker compose -f docker-compose-ray.yml up -d && \
  docker compose -f docker-compose-ray.yml logs -f