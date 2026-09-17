/* LD_PRELOAD 故障注入 shim：inotify_add_watch 恒返回 ENOSPC，
 * 模拟 inotify 手表耗尽——QFileSystemWatcher 加监视恒失败，
 * 与内核真实拒绝行为一致（同 errno）。仅作用于注入进程。
 */
#include <errno.h>

int inotify_add_watch(int fd, const char *pathname, unsigned int mask)
{
    (void)fd; (void)pathname; (void)mask;
    errno = ENOSPC;
    return -1;
}
