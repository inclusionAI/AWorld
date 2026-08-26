#!/bin/bash
cd "$(dirname "$0")"

sh ../mcp-server-base/build-image.sh | exit 1

dt=$(date +%Y%m%d%H%M%S)
img=aworld-mcp-server
version=deepsearch-$dt

docker build -t $img . && \

registry_baseurl=aworld-registry-registry-vpc.ap-southeast-1.cr.aliyuncs.com/aworld

docker tag $img $registry_baseurl/$img && \
docker tag $img $registry_baseurl/$img:$version && \
docker push $registry_baseurl/$img && \
docker push $registry_baseurl/$img:$version && \
echo "✅ Pushed $img >>> $registry_baseurl/$img:$version" && \

exit 0