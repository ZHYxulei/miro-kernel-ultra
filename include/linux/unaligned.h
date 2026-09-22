/* SPDX-License-Identifier: GPL-2.0 */
#ifndef __LINUX_UNALIGNED_H
#define __LINUX_UNALIGNED_H

/*
 * Backport of the generic unaligned-access wrapper introduced in newer
 * kernels (commit 5f505d5dbdf7 "asm-generic: provide a common
 * linux/unaligned.h"), needed by the updated lib/zstd (1.5.7) and lib/lz4
 * imported from v6.18. On arm64 asm/unaligned.h pulls in
 * asm-generic/unaligned.h which provides get_unaligned()/put_unaligned()
 * and the le/be 16/24/32/48/64 helpers.
 */
#include <asm/unaligned.h>

#endif /* __LINUX_UNALIGNED_H */
