#!/bin/sh
BASE_DIR=$(dirname "$(readlink -f "$0")")

cd $BASE_DIR

python setup.py install

cd examples/cmd && aworld web
