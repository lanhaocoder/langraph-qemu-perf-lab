/*
 * lockbench.c — 在 guest 内主动制造密集的内核锁竞争样本
 *
 * 目的：低速 iperf 场景几乎不触发锁慢路径（上一轮只有 2 组 contention），
 *       本程序用多线程 + 高频系统调用放大三类竞争，供 lock:contention_begin/end
 *       与 futex/sched 事件关联 demo 使用。
 *
 * 三类 worker：
 *   A. mmap/munmap 大块内存 + memset：竞争 mm->mmap_lock（rwsem 写锁 + 缺页读锁），
 *      触发 rwsem_down_{read,write}_slowpath → lock:contention_begin/end
 *   B. pthread_mutex 争抢共享计数器：用户态锁竞争 → futex_wait/futex_wake →
 *      sched_waking/sched_wakeup/sched_switch，串起「等锁→唤醒→上CPU」链
 *   C. 多线程对同一文件 open/write/truncate/ftruncate：竞争 inode->i_rwsem
 *
 * 全部静态链接，不依赖 guest 动态库。
 * 用法：lockbench [threads] [seconds]
 */
#define _GNU_SOURCE
#include <pthread.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>

static volatile int stop = 0;
static int nthreads = 16;
static int duration = 15;

static _Atomic long iters_mmap = 0;
static _Atomic long iters_mutex = 0;
static _Atomic long iters_file = 0;

/* ---- A: mmap_lock (rwsem) 写锁竞争 ---- */
static void *worker_mmap(void *arg) {
    long tid = (long)arg;
    /* 线程间错开映射大小，避免完全同步；大块延长持锁时间放大竞争 */
    size_t sz = 4096UL * (8 + (tid % 24));   /* 32KB ~ 124KB */
    while (!stop) {
        void *p = mmap(NULL, sz, PROT_READ | PROT_WRITE,
                       MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (p != MAP_FAILED) {
            memset(p, (int)tid, sz);          /* 触发缺页 → mmap_lock 读锁 */
            munmap(p, sz);                     /* mmap_lock 写锁 */
            atomic_fetch_add(&iters_mmap, 1);
        }
    }
    return NULL;
}

/* ---- B: pthread_mutex → futex 唤醒链 ---- */
static pthread_mutex_t mux = PTHREAD_MUTEX_INITIALIZER;
static long shared_counter = 0;
static void *worker_mutex(void *arg) {
    (void)arg;
    while (!stop) {
        pthread_mutex_lock(&mux);
        shared_counter++;
        /* 拉长临界区，制造更多等待者 */
        for (volatile int i = 0; i < 4000; i++) { }
        pthread_mutex_unlock(&mux);
        atomic_fetch_add(&iters_mutex, 1);
    }
    return NULL;
}

/* ---- C: inode->i_rwsem 竞争（同一文件）---- */
static void *worker_file(void *arg) {
    long slot = (long)arg % 2;
    char path[64];
    snprintf(path, sizeof(path), "/tmp/lockbench_shared_%ld", slot);
    char buf[8192];
    memset(buf, 'x', sizeof(buf));
    while (!stop) {
        int fd = open(path, O_CREAT | O_RDWR, 0644);
        if (fd >= 0) {
            write(fd, buf, sizeof(buf));
            ftruncate(fd, (off_t)(sizeof(buf) / 2));  /* 写锁 i_rwsem */
            close(fd);
            atomic_fetch_add(&iters_file, 1);
        }
    }
    return NULL;
}

int main(int argc, char **argv) {
    if (argc > 1) nthreads = atoi(argv[1]);
    if (argc > 2) duration = atoi(argv[2]);
    if (nthreads < 3) nthreads = 3;
    if (nthreads > 128) nthreads = 128;
    if (duration < 1) duration = 1;

    fprintf(stderr, "[lockbench] threads=%d duration=%ds mode=A:mmap_lock "
                    "B:mutex/futex C:inode_rwsem\n", nthreads, duration);

    pthread_t th[128];
    long args[128];
    for (long i = 0; i < nthreads; i++) {
        args[i] = i;
        int rc;
        switch (i % 3) {
            case 0:  rc = pthread_create(&th[i], NULL, worker_mmap,   (void *)i); break;
            case 1:  rc = pthread_create(&th[i], NULL, worker_mutex,  (void *)i); break;
            default: rc = pthread_create(&th[i], NULL, worker_file,   (void *)i); break;
        }
        if (rc != 0) fprintf(stderr, "[lockbench] pthread_create(%ld) rc=%d\n", i, rc);
    }

    sleep(duration);
    stop = 1;

    for (long i = 0; i < nthreads; i++) pthread_join(th[i], NULL);

    fprintf(stderr, "[lockbench] done: mmap=%ld mutex=%ld file=%ld counter=%ld\n",
            atomic_load(&iters_mmap), atomic_load(&iters_mutex),
            atomic_load(&iters_file), shared_counter);
    return 0;
}
