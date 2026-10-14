/* SPDX-License-Identifier: GPL-2.0 */
/* Copyright (c) Sakion Team. All rights reserved.
 * Adapted from LKM-Source e67cd94c524c27503cb340f8bbab594eca95dbef.
 * Only notifications: no async cleanup, kprobes or Binder queue mutation.
 */
#include <linux/uaccess.h>
#include <trace/hooks/binder.h>
#include "binder_internal.h"
#include "rekernel_internal.h"
static void line_binder_alloc_new_buf_locked(void *data, size_t size,
		size_t *free_async_space, int is_async, bool *should_fail)
{
	struct binder_alloc *alloc = container_of(free_async_space, struct binder_alloc, free_async_space);
	struct task_struct *p;

	if (!is_async || (*free_async_space >= 3 * (size + sizeof(struct binder_buffer)) &&
			 *free_async_space >= WARN_AHEAD_SPACE))
		return;
	rcu_read_lock();
	p = find_task_by_vpid(alloc->pid);
	if (p && line_is_frozen(p) && rekernel_netlink_ready()) {
		char msg[PACKET_SIZE];
		int len = scnprintf(msg, sizeof(msg), "type=Binder,bindertype=free_buffer_full,oneway=1,from_pid=%d,from=%d,target_pid=%d,target=%d,rpc_name=FREE_BUFFER_FULL,code=-1;", task_tgid_nr(current), task_uid(current).val, task_tgid_nr(p), task_uid(p).val);
		sendMessage(msg, len);
	}
	rcu_read_unlock();
}
static void line_binder_reply(void *data, struct binder_proc *target_proc,
		struct binder_proc *proc, struct binder_thread *thread,
		struct binder_transaction_data *tr)
{
	if (target_proc && target_proc->tsk && proc->tsk &&
	    task_uid(target_proc->tsk).val <= MAX_SYSTEM_UID &&
	    proc->pid != target_proc->pid && line_is_frozen(target_proc->tsk) &&
	    rekernel_netlink_ready()) {
		char msg[PACKET_SIZE];
		int len = scnprintf(msg, sizeof(msg), "type=Binder,bindertype=reply,oneway=0,from_pid=%d,from=%d,target_pid=%d,target=%d,rpc_name=SYNC_BINDER_REPLY,code=-1;", task_tgid_nr(proc->tsk), task_uid(proc->tsk).val, task_tgid_nr(target_proc->tsk), task_uid(target_proc->tsk).val);
		sendMessage(msg, len);
	}
}
static void line_binder_transaction(void *data, struct binder_proc *target_proc,
		struct binder_proc *proc, struct binder_thread *thread,
		struct binder_transaction_data *tr)
{
	char buf_data[INTERFACETOKEN_BUFF_SIZE];
	char rpc[INTERFACETOKEN_BUFF_SIZE] = "SYNC_BINDER";
	char msg[PACKET_SIZE];
	size_t size;
	int i, j, len;
	bool async = tr->flags & TF_ONE_WAY;

	if (!target_proc || !target_proc->tsk || !proc->tsk ||
	    task_uid(target_proc->tsk).val <= MIN_USERAPP_UID ||
	    proc->pid == target_proc->pid || !line_is_frozen(target_proc->tsk) ||
	    !rekernel_netlink_ready())
		return;
	if (async) {
		size = min_t(size_t, tr->data_size, sizeof(buf_data));
		if (copy_from_user_nofault(buf_data, (void __user *)(unsigned long)tr->data.ptr.buffer, size))
			return;
		memset(rpc, 0, sizeof(rpc));
		for (i = 0, j = PARCEL_OFFSET; j + 1 < size && i < sizeof(rpc) - 1; j += 2) {
			if (!buf_data[j])
				break;
			rpc[i++] = buf_data[j];
		}
	}
	len = scnprintf(msg, sizeof(msg), "type=Binder,bindertype=transaction,oneway=%d,from_pid=%d,from=%d,target_pid=%d,target=%d,rpc_name=%s,code=%d;", async, task_tgid_nr(proc->tsk), task_uid(proc->tsk).val, task_tgid_nr(target_proc->tsk), task_uid(target_proc->tsk).val, rpc, async ? tr->code : -1);
	sendMessage(msg, len);
}
int register_binder(void)
{
	int ret;

	ret = register_trace_android_vh_binder_alloc_new_buf_locked(line_binder_alloc_new_buf_locked, NULL);
	if (ret)
		return ret;
	ret = register_trace_android_vh_binder_reply(line_binder_reply, NULL);
	if (ret)
		goto stop_alloc;
	ret = register_trace_android_vh_binder_trans(line_binder_transaction, NULL);
	if (!ret)
		return 0;
	unregister_trace_android_vh_binder_reply(line_binder_reply, NULL);
stop_alloc:
	unregister_trace_android_vh_binder_alloc_new_buf_locked(line_binder_alloc_new_buf_locked, NULL);
	return ret;
}
void unregister_binder(void)
{
	unregister_trace_android_vh_binder_trans(line_binder_transaction, NULL);
	unregister_trace_android_vh_binder_reply(line_binder_reply, NULL);
	unregister_trace_android_vh_binder_alloc_new_buf_locked(line_binder_alloc_new_buf_locked, NULL);
}
