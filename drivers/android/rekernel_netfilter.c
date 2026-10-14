/* SPDX-License-Identifier: GPL-2.0 */
/* Copyright (c) Sakion Team. All rights reserved.
 * Adapted from LKM-Source e67cd94c524c27503cb340f8bbab594eca95dbef.
 */
#include <linux/netfilter.h>
#include <linux/netfilter_ipv4.h>
#include <linux/netfilter_ipv6.h>
#include <linux/hashtable.h>
#include <linux/slab.h>
#include <linux/rculist.h>
#include <net/sock.h>
#include <net/ip.h>
#include <net/ipv6.h>
#include <net/tcp.h>
#include <net/net_namespace.h>
#include "rekernel_internal.h"
static DEFINE_HASHTABLE(rekernel_net_uid_map, 6);
static DEFINE_MUTEX(rekernel_net_uid_mutex);
struct uid_info {
	uid_t uid;
	struct hlist_node hnode;
	struct rcu_head rcu;
};
static bool net_uid_monitored(uid_t uid)
{
	struct uid_info *entry;

	hash_for_each_possible_rcu(rekernel_net_uid_map, entry, hnode, uid) {
		if (entry->uid == uid)
			return true;
	}
	return false;
}
void net_uid_add(uid_t uid)
{
	bool found;

	mutex_lock(&rekernel_net_uid_mutex);
	rcu_read_lock();
	found = net_uid_monitored(uid);
	rcu_read_unlock();
	if (!found) {
		struct uid_info *entry = kmalloc(sizeof(*entry), GFP_KERNEL);

		if (entry) {
			entry->uid = uid;
			hash_add_rcu(rekernel_net_uid_map, &entry->hnode, uid);
		}
	}
	mutex_unlock(&rekernel_net_uid_mutex);
}
void net_uid_del(uid_t uid)
{
	struct uid_info *entry;

	mutex_lock(&rekernel_net_uid_mutex);
	hash_for_each_possible(rekernel_net_uid_map, entry, hnode, uid) {
		if (entry->uid == uid) {
			hash_del_rcu(&entry->hnode);
			kfree_rcu(entry, rcu);
			break;
		}
	}
	mutex_unlock(&rekernel_net_uid_mutex);
}
static unsigned int rekernel_pkg_in(void *priv, struct sk_buff *skb,
				   const struct nf_hook_state *state)
{
	struct sock *sk = skb_to_full_sk(skb);
	struct tcphdr tcp, *th;
	unsigned int thoff;
	unsigned short frag_off = 0;
	uid_t uid;
	bool monitored;
	int data_len;
	char msg[PACKET_SIZE];
	int len;

	if (!sk || !sk_fullsock(sk))
		return NF_ACCEPT;
	uid = sock_i_uid(sk).val;
	if (uid < MIN_USERAPP_UID)
		return NF_ACCEPT;
	rcu_read_lock();
	monitored = net_uid_monitored(uid);
	rcu_read_unlock();
	if (!monitored)
		return NF_ACCEPT;
	if (state->pf == NFPROTO_IPV4) {
		struct iphdr ip, *iph = skb_header_pointer(skb, skb_network_offset(skb), sizeof(ip), &ip);

		if (!iph || iph->ihl < 5 || iph->protocol != IPPROTO_TCP ||
		    (ntohs(iph->frag_off) & (IP_MF | IP_OFFSET)))
			return NF_ACCEPT;
		thoff = skb_network_offset(skb) + (iph->ihl << 2);
		data_len = ntohs(iph->tot_len) - (iph->ihl << 2);
#if IS_ENABLED(CONFIG_IPV6)
	} else if (state->pf == NFPROTO_IPV6) {
		struct ipv6hdr ip, *iph = skb_header_pointer(skb, skb_network_offset(skb), sizeof(ip), &ip);

		thoff = 0;
		if (!iph || ipv6_find_hdr(skb, &thoff, -1, &frag_off, NULL) != IPPROTO_TCP || frag_off)
			return NF_ACCEPT;
		data_len = ntohs(iph->payload_len) - (thoff - skb_network_offset(skb) - sizeof(*iph));
#endif
	} else {
		return NF_ACCEPT;
	}
	th = skb_header_pointer(skb, thoff, sizeof(tcp), &tcp);
	if (!th || th->doff < 5)
		return NF_ACCEPT;
	data_len -= th->doff << 2;
	if (data_len < 0 || (data_len == 0 && !th->syn && !th->fin && !th->rst))
		return NF_ACCEPT;
	if (rekernel_netlink_ready()) {
		len = scnprintf(msg, sizeof(msg), "type=Network,target=%d,proto=%s,data_len=%d;", uid, state->pf == NFPROTO_IPV4 ? "ipv4" : "ipv6", data_len);
		sendMessage(msg, len);
	}
	return NF_ACCEPT;
}
static const struct nf_hook_ops rekernel_nf_ops[] = {
	{ .hook = rekernel_pkg_in, .pf = NFPROTO_IPV4,
	  .hooknum = NF_INET_LOCAL_IN, .priority = NF_IP_PRI_SELINUX_LAST + 1 },
#if IS_ENABLED(CONFIG_IPV6)
	{ .hook = rekernel_pkg_in, .pf = NFPROTO_IPV6,
	  .hooknum = NF_INET_LOCAL_IN, .priority = NF_IP6_PRI_SELINUX_LAST + 1 },
#endif
};
static int __net_init rekernel_net_init(struct net *net)
{
	return nf_register_net_hooks(net, rekernel_nf_ops, ARRAY_SIZE(rekernel_nf_ops));
}
static void __net_exit rekernel_net_exit(struct net *net)
{
	nf_unregister_net_hooks(net, rekernel_nf_ops, ARRAY_SIZE(rekernel_nf_ops));
}
static struct pernet_operations rekernel_net_ops = {
	.init = rekernel_net_init,
	.exit = rekernel_net_exit,
};
int register_netfilter(void)
{
	return register_pernet_subsys(&rekernel_net_ops);
}
void unregister_netfilter(void)
{
	struct uid_info *entry;
	struct hlist_node *tmp;
	int bkt;

	unregister_pernet_subsys(&rekernel_net_ops);
	mutex_lock(&rekernel_net_uid_mutex);
	hash_for_each_safe(rekernel_net_uid_map, bkt, tmp, entry, hnode) {
		hash_del_rcu(&entry->hnode);
		kfree_rcu(entry, rcu);
	}
	mutex_unlock(&rekernel_net_uid_mutex);
	rcu_barrier();
}
