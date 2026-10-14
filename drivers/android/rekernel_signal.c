/* SPDX-License-Identifier: GPL-2.0 */
/* Copyright (c) Sakion Team. All rights reserved.
 * LKM-Source e67cd94c524c27503cb340f8bbab594eca95dbef.
 */
#include <linux/signal.h>
#include <trace/hooks/signal.h>
#include "rekernel_internal.h"
static void line_signal(void *data, int sig, struct task_struct *killer, struct task_struct *dst)
{
	if (!dst || !killer)
		return;
	if (line_is_frozen(dst) && (sig == SIGKILL || sig == SIGTERM ||
				 sig == SIGABRT || sig == SIGQUIT) && rekernel_netlink_ready()) {
		char msg[PACKET_SIZE];
		int len = scnprintf(msg, sizeof(msg), "type=Signal,signal=%d,killer_pid=%d,killer=%d,dst_pid=%d,dst=%d;", sig, task_tgid_nr(killer), task_uid(killer).val, task_tgid_nr(dst), task_uid(dst).val);
		sendMessage(msg, len);
	}
}
int register_signal(void)
{
	return register_trace_android_vh_do_send_sig_info(line_signal, NULL);
}
void unregister_signal(void)
{
	unregister_trace_android_vh_do_send_sig_info(line_signal, NULL);
}
