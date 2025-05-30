FROM python:3.11-slim-bookworm AS base

# Use args
ARG MINIMUM_BUILD
ARG USE_CUDA
ARG USE_CUDA_VER
ARG PIPELINES_URLS
ARG PIPELINES_REQUIREMENTS_PATH

## Basis ##
ENV ENV=prod \
    PORT=9099 \
    # pass build args to the build
    MINIMUM_BUILD=${MINIMUM_BUILD} \
    USE_CUDA_DOCKER=${USE_CUDA} \
    USE_CUDA_DOCKER_VER=${USE_CUDA_VER}

# Install GCC and build tools. 
# These are kept in the final image to enable installing packages on the fly.
RUN apt-get update && \
    apt-get install -y gcc build-essential curl git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN apt-get update && apt-get install -y wget unzip openssh-client procps nodejs npm

ARG PIP_OPTIONS='-i https://mirrors.aliyun.com/pypi/simple/'

RUN pip install -U pip pysocks ${PIP_OPTIONS}

# Install Chrome Driver
RUN mkdir /app

RUN cd /app/ && \
    wget https://storage.googleapis.com/chrome-for-testing-public/136.0.7103.92/linux64/chromedriver-linux64.zip && \
    unzip chromedriver-linux64.zip && \
    rm chromedriver-linux64.zip
ENV CHROME_DRIVER_PATH=/app/chromedriver-linux64/chromedriver



FROM base as runner

WORKDIR /app

# Install Python dependencies
COPY ./requirements.txt .
COPY ./requirements-minimum.txt .
RUN pip3 install uv ${PIP_OPTIONS}
RUN uv pip install --system -r requirements-minimum.txt --no-cache-dir ${PIP_OPTIONS}
RUN if [ "$MINIMUM_BUILD" != "true" ]; then \
        if [ "$USE_CUDA_DOCKER" = "true" ]; then \
            pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/$USE_CUDA_DOCKER_VER --no-cache-dir; \
        else \
            pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu --no-cache-dir; \
        fi \
    fi
RUN if [ "$MINIMUM_BUILD" = "true" ]; then \
        uv pip install --system -r requirements-minimum.txt --no-cache-dir ${PIP_OPTIONS}; \
        # Config Env
    else \
        uv pip install --system -r requirements.txt --no-cache-dir; \
    fi

# Copy the application code
RUN echo "start install aworld"
RUN mkdir -p /app/lib
RUN cd /app/lib && git clone https://github.com/inclusionAI/AWorld.git
RUN cd /app/lib/AWorld && git checkout framework_upgrade_aworldserver_gaia && pip install -r aworld/requirements.txt ${PIP_OPTIONS} && python setup.py install

RUN npx playwright install chrome

RUN cd /app



# Layer on for other components
FROM runner AS app

WORKDIR /app

COPY . .

ENV PIPELINES_URLS=${PIPELINES_URLS} \
    PIPELINES_REQUIREMENTS_PATH=${PIPELINES_REQUIREMENTS_PATH}



# Run a docker command if either PIPELINES_URLS or PIPELINES_REQUIREMENTS_PATH is not empty
RUN if [ -n "$PIPELINES_URLS" ] || [ -n "$PIPELINES_REQUIREMENTS_PATH" ]; then \
    echo "Running docker command with PIPELINES_URLS or PIPELINES_REQUIREMENTS_PATH"; \
    ./start.sh --mode setup; \
    fi

# Expose the port
ENV HOST="0.0.0.0"
ENV PORT="9099"

# if we already installed the requirements on build, we can skip this step on run
ENTRYPOINT [ "bash", "start.sh" ]
