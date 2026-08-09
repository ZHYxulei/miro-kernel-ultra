#!/usr/bin/env bash
set -euo pipefail

zip_file="${1:-}"
variant="${2:-standard}"
dist_dir="$(dirname "${zip_file:-out/dist/package.zip}")"
kpatch_tools="${KPATCH_TOOLS:-KPatch-Next/tools/kptools}"

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

[ -n "${zip_file}" ] || fail "usage: $0 <zip-file> <standard|kpatch-exp>"
[ "${variant}" = standard ] || [ "${variant}" = kpatch-exp ] || fail "unknown variant: ${variant}"
[ -s "${zip_file}" ] || fail "${zip_file} is missing or empty"
[ -s "${dist_dir}/Image" ] || fail "${dist_dir}/Image is missing or empty"
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

zip_image="$(mktemp)"
trap 'rm -f "${zip_image}"' EXIT
unzip -p "${zip_file}" Image > "${zip_image}"
if [ "${variant}" = standard ]; then
    cmp -s "${zip_image}" "${dist_dir}/Image" || fail "standard ZIP Image differs from ${dist_dir}/Image"
    grep -Fq 'KPatch-Next EXP' <<< "${anykernel_config}" && fail "standard ZIP contains KPatch-Next label"
else
    [ -s "${dist_dir}/Image-kpatch-next-exp" ] || fail "KPatch-Next Image is missing or empty"
    cmp -s "${zip_image}" "${dist_dir}/Image-kpatch-next-exp" || fail "KPatch ZIP Image differs from patched Image"
    cmp -s "${zip_image}" "${dist_dir}/Image" && fail "KPatch Image is identical to standard Image"
    require_config 'kernel.string=miro-kernel-ultra KPatch-Next EXP'
    [ -x "${kpatch_tools}" ] || fail "kptools is missing: ${kpatch_tools}"
    patch_info="$("${kpatch_tools}" -l -i "${zip_image}")"
    grep -Fxq 'patched=true' <<< "${patch_info}" || fail "KPatch Image is not recognized as patched"
    printf '%s\n' "${patch_info}" | grep -E '^(version|compile_time|arch)=' || true
fi

module_file="$(find "${dist_dir}/modules" -type f -name '*.ko' -print -quit 2>/dev/null || true)"
if [ -n "${module_file}" ]; then
    grep -Eq '^modules/.+\.ko$' <<< "${entries_text}" || fail "ZIP is missing kernel modules"
fi

echo "Verified ${variant}: ${zip_file}"
