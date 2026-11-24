#define _GNU_SOURCE
#include <pthread.h>
#include <sched.h>
#include <stdio.h>
#include <errno.h>
#include <string.h>
#include <stdlib.h>
#include <time.h>
#include <sys/mman.h>

#define PRIO_L 10
#define PRIO_M 20
#define PRIO_H 30

typedef struct {
    char name;
    int prio;
} target_t;

static pthread_mutex_t mutex;
static struct timespec t0;
static struct timespec h_release;   /* H's *scheduled* release time */
static struct timespec h_done;

static void add_ms(struct timespec *ts, int ms)
{
    ts->tv_nsec += (long)ms * 1000000L;
    while (ts->tv_nsec >= 1000000000L) {
        ts->tv_nsec -= 1000000000L;
        ts->tv_sec += 1;
    }
}

static double diff_ms(const struct timespec *a, const struct timespec *b)
{
    return (b->tv_sec - a->tv_sec) * 1000.0 +
           (b->tv_nsec - a->tv_nsec) / 1e6;
}

static void busy_ms(int ms)
{
    struct timespec start, now;
    clock_gettime(CLOCK_THREAD_CPUTIME_ID, &start);
    do {
        clock_gettime(CLOCK_THREAD_CPUTIME_ID, &now);
    } while (diff_ms(&start, &now) < (double)ms);
}

static void *worker(void *arg)
{
    target_t *t = arg;
    struct timespec wake = t0;
    int rc;

    /* Absolute release times: L at t0, H at t0+5ms, M at t0+10ms. */
    if (t->name == 'H')
        add_ms(&wake, 5);
    else if (t->name == 'M')
        add_ms(&wake, 10);

    do {
        rc = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &wake, NULL);
    } while (rc == EINTR);
    if (rc != 0) {
        fprintf(stderr, "%c clock_nanosleep: %s\n", t->name, strerror(rc));
        return NULL;
    }

    if (t->name == 'L') {
        rc = pthread_mutex_lock(&mutex);
        if (rc != 0) {
            fprintf(stderr, "L lock: %s\n", strerror(rc));
            return NULL;
        }
        busy_ms(40);                 /* 40ms of CPU work inside the lock */
        pthread_mutex_unlock(&mutex);
    } else if (t->name == 'H') {
        h_release = wake;
        rc = pthread_mutex_lock(&mutex);
        if (rc != 0) {
            fprintf(stderr, "H lock: %s\n", strerror(rc));
            return NULL;
        }
        busy_ms(5);
        pthread_mutex_unlock(&mutex);
        clock_gettime(CLOCK_MONOTONIC, &h_done);
    } else {
        /* M: medium-priority CPU hog, released after H has blocked on L. */
        busy_ms(30);
    }
    return NULL;
}

static int create_prio(pthread_t *th, target_t *a)
{
    pthread_attr_t attr;
    struct sched_param sp;

    sp.sched_priority = a->prio;
    pthread_attr_init(&attr);
    pthread_attr_setinheritsched(&attr, PTHREAD_EXPLICIT_SCHED);
    pthread_attr_setschedpolicy(&attr, SCHED_FIFO);
    pthread_attr_setschedparam(&attr, &sp);

    int rc = pthread_create(th, &attr, worker, (void *)a);
    pthread_attr_destroy(&attr);
    return rc;
}

static int run_mode(int protocol, const char *label)
{
    pthread_mutexattr_t mattr;
    pthread_t th[3];
    int created = 0;
    target_t args[3] = {
        {'L', PRIO_L},
        {'H', PRIO_H},
        {'M', PRIO_M},
    };
    int rc, ret = 0;

    pthread_mutexattr_init(&mattr);
    rc = pthread_mutexattr_setprotocol(&mattr, protocol);
    if (rc != 0) {
        fprintf(stderr, "setprotocol(%s): %s\n", label, strerror(rc));
        pthread_mutexattr_destroy(&mattr);
        return -1;
    }
    if (protocol == PTHREAD_PRIO_PROTECT) {
        rc = pthread_mutexattr_setprioceiling(&mattr, PRIO_H);
        if (rc != 0) {
            fprintf(stderr, "setprioceiling: %s\n", strerror(rc));
            pthread_mutexattr_destroy(&mattr);
            return -1;
        }
    }
    rc = pthread_mutex_init(&mutex, &mattr);
    pthread_mutexattr_destroy(&mattr);
    if (rc != 0) {
        fprintf(stderr, "mutex_init(%s): %s\n", label, strerror(rc));
        return -1;
    }

    clock_gettime(CLOCK_MONOTONIC, &t0);
    add_ms(&t0, 40);

    for (int i = 0; i < 3; i++) {
        rc = create_prio(&th[i], &args[i]);
        if (rc == EPERM) {
            fprintf(stderr,
                    "EPERM: SCHED_FIFO needs sudo or CAP_SYS_NICE "
                    "(e.g. sudo ./pi_demo). Not falling back to SCHED_OTHER.\n");
            ret = -1;
            break;
        }
        if (rc) {
            fprintf(stderr, "create %c: %s\n", args[i].name, strerror(rc));
            ret = -1;
            break;
        }
        created++;
    }

    for (int i = 0; i < created; i++)
        pthread_join(th[i], NULL);

    if (ret == 0)
        printf("%-10s  H response = %6.2f ms\n", label,
               diff_ms(&h_release, &h_done));

    pthread_mutex_destroy(&mutex);
    return ret;
}

int main(void)
{
    cpu_set_t set;
    CPU_ZERO(&set);
    CPU_SET(0, &set);
    /* Threads created afterwards inherit this affinity. */
    if (sched_setaffinity(0, sizeof(set), &set) != 0) {
        perror("sched_setaffinity");
        return 1;
    }

    /* Avoid page-fault jitter during the timed section . */
    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0)
        perror("mlockall (continuing)");

    printf("Pathfinder cross-check (single core, SCHED_FIFO)\n");
    printf("Expect NONE ~70ms, INHERIT ~40ms, PROTECT ~40ms for H's response\n");
    printf("(VM/hypervisor jitter can still move these around)\n\n");

    if (run_mode(PTHREAD_PRIO_NONE, "NONE") < 0)
        return 1;
    if (run_mode(PTHREAD_PRIO_INHERIT, "INHERIT") < 0)
        return 1;
    if (run_mode(PTHREAD_PRIO_PROTECT, "PROTECT") < 0)
        return 1;

    return 0;
}