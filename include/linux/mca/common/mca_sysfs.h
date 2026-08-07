/* SPDX-License-Identifier: GPL-2.0 */
/*
 * MCA sysfs stub - provides minimal definitions for drivers that
 * reference the MCA sysfs framework. The original MCA framework
 * (vendor/xiaomi/proprietary/mca/) is not available in this tree.
 *
 * sysfs attributes created via this stub will not function at runtime.
 */
#ifndef _MCA_SYSFS_H
#define _MCA_SYSFS_H

#include <linux/kernel.h>
#include <linux/string.h>
#include <linux/sysfs.h>

/* Device ID constants used by MCA sysfs framework */
enum {
    SYSFS_DEV_0 = 0,
    SYSFS_DEV_1,
    SYSFS_DEV_2,
    SYSFS_DEV_3,
};

/* Attribute info structure */
struct mca_sysfs_attr_info {
    int sysfs_attr_name;
    umode_t mode;
    const char *attr_name;
};

/*
 * Macro to declare a read-only sysfs attribute entry.
 * The 'prop' argument is an enum value identifying the property.
 */
#define mca_sysfs_attr_ro(group, mode, prop, field) \
{ \
    .sysfs_attr_name = prop, \
    .mode = mode, \
    .attr_name = #field, \
}

/*
 * Look up an attribute by name in the table.
 * Returns NULL if not found (stub: always returns NULL).
 */
static inline struct mca_sysfs_attr_info *mca_sysfs_lookup_attr(
    const char *name,
    struct mca_sysfs_attr_info *tbl,
    size_t size)
{
    size_t i;
    for (i = 0; i < size; i++) {
        if (tbl[i].attr_name && !strcmp(tbl[i].attr_name, name))
            return &tbl[i];
    }
    return NULL;
}

/*
 * Create sysfs files for the given attribute table.
 * Stub: does nothing, returns 0 (success).
 */
static inline int mca_sysfs_create_files(
    int dev_id,
    struct mca_sysfs_attr_info *tbl,
    size_t size)
{
    return 0;
}

#endif /* _MCA_SYSFS_H */
