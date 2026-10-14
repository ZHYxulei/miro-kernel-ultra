/* SPDX-License-Identifier: GPL-2.0 */
/* Copyright (c) Sakion Team. All rights reserved.
 * Adapted from LKM-Source e67cd94c524c27503cb340f8bbab594eca95dbef.
 * Generic netlink only; monitor changes require administrative privilege.
 */
#include <net/genetlink.h>
#include "rekernel_internal.h"
static bool rekernel_genl_registered;
static struct genl_family rekernel_genl_family;
bool rekernel_netlink_ready(void)
{
	return READ_ONCE(rekernel_genl_registered);
}
static int rekernel_genl_add_monitor_net(struct sk_buff *skb, struct genl_info *info)
{
	if (!info->attrs[REKERNEL_A_UID])
		return -EINVAL;
	net_uid_add(nla_get_u32(info->attrs[REKERNEL_A_UID]));
	return 0;
}
static int rekernel_genl_del_monitor_net(struct sk_buff *skb, struct genl_info *info)
{
	if (!info->attrs[REKERNEL_A_UID])
		return -EINVAL;
	net_uid_del(nla_get_u32(info->attrs[REKERNEL_A_UID]));
	return 0;
}
static int rekernel_genl_get_version(struct sk_buff *skb, struct genl_info *info)
{
	struct sk_buff *reply;
	void *head;

	reply = genlmsg_new(nla_total_size(sizeof(REKERNEL_MAJOR_VERSION)), GFP_KERNEL);
	if (!reply)
		return -ENOMEM;
	head = genlmsg_put(reply, info->snd_portid, info->snd_seq,
			   &rekernel_genl_family, 0, REKERNEL_C_GET_VERSION);
	if (!head) {
		nlmsg_free(reply);
		return -ENOMEM;
	}
	if (nla_put_string(reply, REKERNEL_A_MSG, REKERNEL_MAJOR_VERSION)) {
		genlmsg_cancel(reply, head);
		nlmsg_free(reply);
		return -EMSGSIZE;
	}
	genlmsg_end(reply, head);
	return genlmsg_reply(reply, info);
}
static const struct nla_policy rekernel_genl_policy[REKERNEL_A_MAX + 1] = {
	[REKERNEL_A_MSG] = { .type = NLA_NUL_STRING, .len = PACKET_SIZE - 1 },
	[REKERNEL_A_UID] = { .type = NLA_U32 },
	[REKERNEL_A_PID] = { .type = NLA_U32 },
};
static const struct genl_ops rekernel_genl_ops[] = {
	{ .cmd = REKERNEL_C_ADD_MONITOR_NET, .flags = GENL_ADMIN_PERM,
	  .doit = rekernel_genl_add_monitor_net },
	{ .cmd = REKERNEL_C_DEL_MONITOR_NET, .flags = GENL_ADMIN_PERM,
	  .doit = rekernel_genl_del_monitor_net },
	{ .cmd = REKERNEL_C_GET_VERSION, .doit = rekernel_genl_get_version },
};
static const struct genl_multicast_group rekernel_genl_mcgrps[] = {
	{ .name = REKERNEL_GENL_MCGRP_NAME, .flags = GENL_ADMIN_PERM },
};
static struct genl_family rekernel_genl_family = {
	.name = REKERNEL_GENL_FAMILY_NAME,
	.version = REKERNEL_GENL_VERSION,
	.maxattr = REKERNEL_A_MAX,
	.policy = rekernel_genl_policy,
	.module = THIS_MODULE,
	.ops = rekernel_genl_ops,
	.n_ops = ARRAY_SIZE(rekernel_genl_ops),
	.mcgrps = rekernel_genl_mcgrps,
	.n_mcgrps = ARRAY_SIZE(rekernel_genl_mcgrps),
};
int sendMessage(char *packet_buffer, uint16_t len)
{
	struct sk_buff *skb;
	void *head;
	int ret;

	skb = genlmsg_new(nla_total_size(len), GFP_ATOMIC);
	if (!skb)
		return -ENOMEM;
	head = genlmsg_put(skb, 0, 0, &rekernel_genl_family, 0, REKERNEL_C_EVENT);
	if (!head) {
		nlmsg_free(skb);
		return -EMSGSIZE;
	}
	if (nla_put(skb, REKERNEL_A_MSG, len, packet_buffer)) {
		genlmsg_cancel(skb, head);
		nlmsg_free(skb);
		return -EMSGSIZE;
	}
	genlmsg_end(skb, head);
	ret = genlmsg_multicast(&rekernel_genl_family, skb, 0, 0, GFP_ATOMIC);
	return ret == -ESRCH ? 0 : ret;
}
int rekernel_netlink_start(void)
{
	int ret = genl_register_family(&rekernel_genl_family);

	if (!ret)
		WRITE_ONCE(rekernel_genl_registered, true);
	return ret;
}
void rekernel_netlink_stop(void)
{
	if (rekernel_netlink_ready()) {
		WRITE_ONCE(rekernel_genl_registered, false);
		genl_unregister_family(&rekernel_genl_family);
	}
}
