conda activate aworld

#!/bin/bash
BASE_DIR=$(dirname "$(readlink -f "$0")")

cd $BASE_DIR/aworld/cmd/web/webui && npm run build

cd $BASE_DIR && python setup.py install

cd examples/cmd && aworld web
