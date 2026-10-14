/* SPDX-License-Identifier: GPL-2.0 */
/* Copyright (c) Sakion Team. All rights reserved.
 * Adapted from LKM-Source at e67cd94c524c27503cb340f8bbab594eca95dbef.
 */
#ifndef REKERNEL_INTERNAL_H
#define REKERNEL_INTERNAL_H
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/sched.h>
#include <linux/sched/jobctl.h>
#include <linux/cred.h>
#include <linux/freezer.h>
#include <linux/cgroup.h>
#include "rekernel.h"
#define PACKET_SIZE 256
static inline bool line_is_frozen(struct task_struct *task)
{
	if (cgroup_task_frozen(task) || (READ_ONCE(task->jobctl) & JOBCTL_TRAP_FREEZE))
		return true;
	if (!task->group_leader)
		return true;
	return (READ_ONCE(task->group_leader->__state) & TASK_FROZEN) ||
		freezing(task->group_leader);
}
int sendMessage(char *packet_buffer, uint16_t len);
bool rekernel_netlink_ready(void);
int rekernel_netlink_start(void);
void rekernel_netlink_stop(void);
void net_uid_add(uid_t uid);
void net_uid_del(uid_t uid);
int register_binder(void);
void unregister_binder(void);
int register_signal(void);
void unregister_signal(void);
int register_netfilter(void);
void unregister_netfilter(void);
#endif
