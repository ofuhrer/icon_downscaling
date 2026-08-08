#!/usr/bin/env bash
# Load repository Balfrin defaults without replacing explicit environment values.

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "load_balfrin_site_config.sh must be sourced" >&2
  exit 2
fi

_balfrin_loader_root=$(
  CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd
)
_balfrin_site_config=${HICAR_SITE_CONFIG:-$_balfrin_loader_root/config/balfrin.env}
test -f "$_balfrin_site_config" || {
  echo "missing Balfrin site configuration: $_balfrin_site_config" >&2
  return 2
}

while IFS='=' read -r _balfrin_key _balfrin_value; do
  case "$_balfrin_key" in
    ''|\#*) continue ;;
    USER_ENV_ROOT|REA_FDB_IMAGE|ICON_GRID|ICON_DOWNSCALING_DURABLE_ROOT|HICAR_BRANCH|HICAR_COMMIT)
      if test -z "${!_balfrin_key+x}"; then
        printf -v "$_balfrin_key" '%s' "$_balfrin_value"
        export "$_balfrin_key"
      fi
      ;;
    *)
      echo "unsupported Balfrin site-config key: $_balfrin_key" >&2
      return 2
      ;;
  esac
done < "$_balfrin_site_config"

unset _balfrin_loader_root _balfrin_site_config _balfrin_key _balfrin_value
