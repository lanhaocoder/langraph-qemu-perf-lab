#define _GNU_SOURCE
#include <stdio.h>
#include <errno.h>
#include <string.h>
#include <unistd.h>
#include <sys/syscall.h>
#include <linux/perf_event.h>
static void t(int type, unsigned long long cfg, const char *name) {
    struct perf_event_attr a; memset(&a, 0, sizeof(a));
    a.type = type; a.size = sizeof(a); a.config = cfg; a.disabled = 1;
    errno = 0;
    long fd = syscall(__NR_perf_event_open, &a, 0, -1, -1, 0);
    printf("  %-28s -> %ld  errno=%d %s\n", name, fd, errno, fd < 0 ? strerror(errno) : "OK");
    if (fd >= 0) close(fd);
}
int main(void) {
    printf("[perf probe]\n");
    t(PERF_TYPE_SOFTWARE, PERF_COUNT_SW_TASK_CLOCK, "SOFTWARE/task-clock");
    t(PERF_TYPE_SOFTWARE, PERF_COUNT_SW_CONTEXT_SWITCHES, "SOFTWARE/ctx-switches");
    t(PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES, "HARDWARE/cycles");
    return 0;
}
