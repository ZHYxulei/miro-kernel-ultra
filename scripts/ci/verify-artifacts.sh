#!/usr/bin/env bash
set -euo pipefail

dist_dir="${1:-out/dist}"

fail() {
    printf 'Artifact verification failed: %s\n' "$*" >&2
    exit 1
}

require_entry() {
    local entry="$1"
    grep -Fxq -- "${entry}" <<< "${entries_text}" || fail "ZIP is missing ${entry}"
}

require_config() {
    local setting="$1"
    grep -Fxq -- "${setting}" <<< "${anykernel_config}" || fail "anykernel.sh is missing ${setting}"
}

[ -s "${dist_dir}/Image" ] || fail "${dist_dir}/Image is missing or empty"

mapfile -t zip_files < <(find "${dist_dir}" -maxdepth 1 -type f -name '*.zip' -print | sort)
[ "${#zip_files[@]}" -eq 1 ] || fail "expected exactly one ZIP in ${dist_dir}, found ${#zip_files[@]}"

zip_file="${zip_files[0]}"
unzip -tq "${zip_file}" || fail "ZIP integrity check failed: ${zip_file}"

mapfile -t entries < <(unzip -Z1 "${zip_file}")
entries_text="$(printf '%s\n' "${entries[@]}")"
require_entry 'Image'
require_entry 'anykernel.sh'
require_entry 'tools/ak3-core.sh'
grep -Eq '(^|/)\.git(/|$)' <<< "${entries_text}" && fail "ZIP contains Git metadata"

anykernel_config="$(unzip -p "${zip_file}" anykernel.sh)"
require_config 'do.devicecheck=1'
require_config 'device.name1=24122RKC7C'
require_config 'device.name2=miro'
require_config 'device.name3=Redmi K80 Pro'
require_config 'BLOCK=boot;'
require_config 'IS_SLOT_DEVICE=1;'

module_file="$(find "${dist_dir}/modules" -type f -name '*.ko' -print -quit 2>/dev/null || true)"
if [ -n "${module_file}" ]; then
    grep -Eq '^modules/.+\.ko$' <<< "${entries_text}" || fail "ZIP is missing kernel modules"
fi

echo "Verified ${zip_file}"
