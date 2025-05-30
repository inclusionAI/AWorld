#!/bin/bash
cd "$(dirname "$0")"

# Check your env file
if [ ! -f ".env" ]; then
    echo "Please add your own .env config file from template .env.template before running gaia test!"
    exit 1
fi

# Check GAIA dataset
if [ ! -d "examples/gaia/GAIA" ]; then
    echo "Please download GAIA dataset from https://huggingface.co/datasets/gaia-benchmark/GAIA and put it in examples/gaia/GAIA"
    exit 1
fi

# Build docker image
docker compose up --build -d && docker compose logs -f