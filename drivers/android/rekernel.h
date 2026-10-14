/* SPDX-License-Identifier: GPL-2.0 */
/* Re:Kernel LKM-Source, Sakion-Team/Re-Kernel
 * e67cd94c524c27503cb340f8bbab594eca95dbef (release 11.7).
 */
#ifndef REKERNEL_H
#define REKERNEL_H
#define REKERNEL_MAJOR_VERSION "11.7"
/* Async cleanup deliberately disabled: no Binder internal symbol calls. */
#define MIN_USERAPP_UID 10000
#define MAX_SYSTEM_UID 2000
#define WARN_AHEAD_SPACE (1 << 17)
#define INTERFACETOKEN_BUFF_SIZE 140
#define PARCEL_OFFSET 16
#define LINE_ERROR (-1)
#define LINE_SUCCESS 0
#define REKERNEL_GENL_FAMILY_NAME "rekernel"
#define REKERNEL_GENL_VERSION 1
#define REKERNEL_GENL_MCGRP_NAME "events"
enum rekernel_genl_cmd {
	REKERNEL_C_UNSPEC,
	REKERNEL_C_EVENT,
	REKERNEL_C_ADD_MONITOR_NET,
	REKERNEL_C_DEL_MONITOR_NET,
	REKERNEL_C_GET_VERSION,
	__REKERNEL_C_MAX,
};
#define REKERNEL_C_MAX (__REKERNEL_C_MAX - 1)
enum rekernel_genl_attr {
	REKERNEL_A_UNSPEC,
	REKERNEL_A_MSG,
	REKERNEL_A_UID,
	REKERNEL_A_PID,
	__REKERNEL_A_MAX,
};
#define REKERNEL_A_MAX (__REKERNEL_A_MAX - 1)
#endif
