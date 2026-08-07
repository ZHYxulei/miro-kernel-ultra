/* SPDX-License-Identifier: GPL-2.0 */
/*
 * MCA logging stub - provides minimal logging macros.
 * The original MCA framework is not available in this tree.
 */
#ifndef _MCA_LOG_H
#define _MCA_LOG_H

#include <linux/printk.h>

#define mca_log_info(fmt, ...)	pr_info(fmt, ##__VA_ARGS__)
#define mca_log_err(fmt, ...)	pr_err(fmt, ##__VA_ARGS__)
#define mca_log_dbg(fmt, ...)	pr_debug(fmt, ##__VA_ARGS__)
#define mca_log_warn(fmt, ...)	pr_warn(fmt, ##__VA_ARGS__)

#endif /* _MCA_LOG_H */
