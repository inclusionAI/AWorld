#!/bin/sh
set -eu

destination=${1:?usage: download-whisper-model.sh DESTINATION}
# ModelScope serves byte-identical files for the pinned Systran revision and is
# reachable from the China CI runner. The hashes below remain the trust root.
base_url="https://www.modelscope.cn/models/gpustack/faster-whisper-base/resolve/master"

mkdir -p "$destination"

download() {
  file_name=$1
  expected_sha256=$2
  curl --fail --location --retry 3 --retry-all-errors \
    --output "$destination/$file_name" "$base_url/$file_name"
  printf '%s  %s\n' "$expected_sha256" "$destination/$file_name" | sha256sum -c -
}

download config.json 56a6d8110d311f19c8f0471e562832c7527f146b567275bfca59fcf7c184da9a
download model.bin d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9
download tokenizer.json fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab
download vocabulary.txt 34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913
