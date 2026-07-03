#!/usr/bin/env bash
# Build and push the mmc analyser container images to ECR.
#
# Usage: ./scripts/build-and-push-analysers.sh <artifact-account-id> [<region>] [--dry-run] [--allow-dirty]
#
# Ships:
#   - analyser-base:   pushed to  <acct>.dkr.ecr.<region>.amazonaws.com/mmc/analyser-base:{<sha>,latest}
#   - analyser-<name>: pushed to  <acct>.dkr.ecr.<region>.amazonaws.com/mmc/analyser-<name>:{<sha>,latest}
#                      built with --build-arg BASE_IMAGE=<host>/mmc/analyser-base:<sha>
#
# ANALYSERS = bias dq explain mq shadow.

set -euo pipefail

ANALYSERS=(bias dq explain mq shadow)
DRY_RUN=0
ALLOW_DIRTY=0
POSITIONAL=()

die() {
  echo "error: $*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' not on PATH — install it and retry"
}

run() {
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "[dry-run] $*"
  else
    "$@"
  fi
}

usage() {
  cat >&2 <<EOF
Usage: $0 <artifact-account-id> [<region>] [--dry-run] [--allow-dirty]

  <artifact-account-id>  12-digit AWS account ID hosting the ECR repos.
  <region>               AWS region (default: eu-west-1).
  --dry-run              Print docker/aws calls without executing.
  --allow-dirty          Skip the clean-tree check.
EOF
  exit 2
}

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --allow-dirty) ALLOW_DIRTY=1 ;;
    -h | --help) usage ;;
    --*) die "unknown flag: $arg" ;;
    *) POSITIONAL+=("$arg") ;;
  esac
done

[[ ${#POSITIONAL[@]} -ge 1 ]] || usage
ACCOUNT="${POSITIONAL[0]}"
REGION="${POSITIONAL[1]:-eu-west-1}"

[[ $ACCOUNT =~ ^[0-9]{12}$ ]] || die "artifact-account-id must be 12 digits, got: $ACCOUNT"

require_cmd docker
require_cmd aws
require_cmd git

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

if [[ $ALLOW_DIRTY -eq 0 ]]; then
  dirty="$(git status --porcelain)"
  if [[ -n $dirty ]]; then
    echo "error: working tree is not clean. Commit or stash first, or pass --allow-dirty." >&2
    echo "$dirty" >&2
    exit 1
  fi
fi

SHA="$(git rev-parse --short HEAD)"
HOST="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"

echo "==> logging into ECR ${HOST}"
if [[ $DRY_RUN -eq 1 ]]; then
  echo "[dry-run] aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $HOST"
else
  if ! aws ecr get-login-password --region "$REGION" >/dev/null 2>&1; then
    die "aws ecr get-login-password failed — run 'aws sso login --profile <name>' first"
  fi
  aws ecr get-login-password --region "$REGION" |
    docker login --username AWS --password-stdin "$HOST"
fi

base_tag_sha="${HOST}/mmc/analyser-base:${SHA}"
base_tag_latest="${HOST}/mmc/analyser-base:latest"

echo "==> building base image ${base_tag_sha}"
run docker buildx build \
  --platform=linux/amd64 \
  -t "$base_tag_sha" \
  -t "$base_tag_latest" \
  containers/base

echo "==> pushing analyser-base"
run docker push "$base_tag_sha"
run docker push "$base_tag_latest"

declare -a SUMMARY=()
if [[ $DRY_RUN -eq 1 ]]; then
  SUMMARY+=("base    ${base_tag_sha}  ${base_tag_latest}  digest=(dry-run)")
else
  base_digest="$(docker inspect --format='{{index .RepoDigests 0}}' "$base_tag_sha" 2>/dev/null || echo unknown)"
  SUMMARY+=("base    ${base_tag_sha}  ${base_tag_latest}  ${base_digest}")
fi

for name in "${ANALYSERS[@]}"; do
  tag_sha="${HOST}/mmc/analyser-${name}:${SHA}"
  tag_latest="${HOST}/mmc/analyser-${name}:latest"
  echo "==> building analyser-${name}"
  run docker buildx build \
    --platform=linux/amd64 \
    --build-arg "BASE_IMAGE=${base_tag_sha}" \
    -t "$tag_sha" \
    -t "$tag_latest" \
    "containers/${name}"

  echo "==> pushing analyser-${name}"
  run docker push "$tag_sha"
  run docker push "$tag_latest"

  if [[ $DRY_RUN -eq 1 ]]; then
    SUMMARY+=("${name}  ${tag_sha}  ${tag_latest}  digest=(dry-run)")
  else
    digest="$(docker inspect --format='{{index .RepoDigests 0}}' "$tag_sha" 2>/dev/null || echo unknown)"
    SUMMARY+=("${name}  ${tag_sha}  ${tag_latest}  ${digest}")
  fi
done

echo
echo "==> summary"
printf '  %s\n' "${SUMMARY[@]}"
