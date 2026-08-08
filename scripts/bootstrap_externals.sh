#!/bin/sh
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
git -C "$repo" submodule update --init HICAR
git -C "$repo/HICAR" rev-parse HEAD
