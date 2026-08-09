#!/usr/bin/env bash
set -euo pipefail

dist_dir="${1:-out/dist}"

test -s "${dist_dir}/Image"

mapfile -t zip_files < <(find "${dist_dir}" -maxdepth 1 -type f -name '*.zip' -print | sort)
test "${#zip_files[@]}" -eq 1

zip_file="${zip_files[0]}"
unzip -tq "${zip_file}"

mapfile -t entries < <(unzip -Z1 "${zip_file}")
printf '%s\n' "${entries[@]}" | grep -qx 'Image'
printf '%s\n' "${entries[@]}" | grep -qx 'anykernel.sh'
printf '%s\n' "${entries[@]}" | grep -qx 'tools/ak3-core.sh'
if printf '%s\n' "${entries[@]}" | grep -Eq '(^|/)\.git(/|$)'; then
    exit 1
fi

anykernel_config="$(unzip -p "${zip_file}" anykernel.sh)"
grep -qx 'do.devicecheck=1' <<< "${anykernel_config}"
grep -qx 'device.name1=24122RKC7C' <<< "${anykernel_config}"
grep -qx 'device.name2=miro' <<< "${anykernel_config}"
grep -qx 'device.name3=Redmi K80 Pro' <<< "${anykernel_config}"
grep -qx 'BLOCK=boot;' <<< "${anykernel_config}"
grep -qx 'IS_SLOT_DEVICE=1;' <<< "${anykernel_config}"

if find "${dist_dir}/modules" -type f -name '*.ko' -print -quit 2>/dev/null | grep -q .; then
    printf '%s\n' "${entries[@]}" | grep -Eq '^modules/.+\.ko$'
fi

echo "Verified ${zip_file}"
