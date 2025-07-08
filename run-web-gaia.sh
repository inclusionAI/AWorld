#!/bin/sh
BASE_DIR=$(dirname "$(readlink -f "$0")")

cd $BASE_DIR

# Following the instructions in https://github.com/inclusionAI/AWorld/blob/main/examples/gaia/README.md#running-the-gaia
if [ "$CONDA_DEFAULT_ENV" != "aworld-gaia" ]; then
    echo "Creating and activating aworld-gaia environment before running gaia web ui"
    echo "command: conda env create -f examples/gaia/aworld-gaia.yml && conda activate aworld-gaia"
    exit 1
else
    echo "Already activated aworld-gaia environment"
fi

pip install "marker-pdf[full]" --no-deps

cd aworld/cmd/web/webui && npm run build

cd $BASE_DIR && python setup.py install

# Install dependencies
brew install libmagic
brew install ffmpeg
brew install --cask libreoffice-language-pack-zh-cn

# Run the web UI
cd examples/gaia/cmd && aworld web
