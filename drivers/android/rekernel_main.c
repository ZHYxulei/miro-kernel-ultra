/* SPDX-License-Identifier: GPL-2.0 */
/* Copyright (c) Sakion Team. All rights reserved.
 * LKM-Source e67cd94c524c27503cb340f8bbab594eca95dbef, without async cleanup.
 */
#include <linux/tracepoint.h>
#include "rekernel_internal.h"
static int __init start_rekernel(void)
{
	int ret;

	ret = rekernel_netlink_start();
	if (ret)
		return ret;
	ret = register_binder();
	if (ret)
		goto stop_netlink;
	ret = register_signal();
	if (ret)
		goto stop_binder;
	ret = register_netfilter();
	if (ret)
		goto stop_signal;
	pr_info("Re:Kernel %s started (async cleanup disabled)\n", REKERNEL_MAJOR_VERSION);
	return 0;
stop_signal:
	unregister_signal();
stop_binder:
	unregister_binder();
stop_netlink:
	tracepoint_synchronize_unregister();
	rekernel_netlink_stop();
	return ret;
}
static void __exit exit_rekernel(void)
{
	unregister_netfilter();
	unregister_signal();
	unregister_binder();
	tracepoint_synchronize_unregister();
	rekernel_netlink_stop();
}
module_init(start_rekernel);
module_exit(exit_rekernel);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Re:Kernel frozen task notifications");
