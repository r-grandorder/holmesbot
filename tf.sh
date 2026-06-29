#!/bin/bash
# Wrapper to run terraform against ./terraform with terraform.tfvars auto-loaded.
# Usage: ./tf.sh plan | ./tf.sh apply | ./tf.sh output github_ci_role_arn
#
# Unlike axolkin (which sources a .secrets file to export TF_VAR_*), this project
# keeps secrets in terraform/terraform.tfvars, which terraform loads automatically.
# This wrapper just runs terraform in the right directory.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TF_DIR="$SCRIPT_DIR/terraform"
if [ ! -f "$TF_DIR/terraform.tfvars" ]; then
  echo "Error: $TF_DIR/terraform.tfvars not found (gitignored secrets file)." >&2
  echo "Copy terraform/terraform.tfvars.example to terraform/terraform.tfvars and fill it in." >&2
  exit 1
fi
cd "$TF_DIR"
exec terraform "$@"
