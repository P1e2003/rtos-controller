#define _GNU_SOURCE
#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <semaphore.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <time.h>

#define PRIO_L 10
#define PRIO_M 20
#define PRIO_H 30

#define L_WORK_MS 40
#define H_RELEASE_OFFSET_MS 10
#define H_WORK_MS 5
#define M_RELEASE_OFFSET_MS 20
#define M_WORK_MS 120
#define MODE_GAP_MS 50

#define EXIT_EPERM 77

typedef enum {
    MODE_ALL = 0,
    MODE_NONE,
    MODE_INHERIT,
    MODE_PROTECT,
} run_selection_t;

typedef struct {
    char name;
    int prio;
} worker_arg_t;

typedef struct {
    int protocol;
    const char *label;
    const char *expected_pattern;
} mode_spec_t;

static pthread_mutex_t mutex;
static sem_t start_sem;
static struct timespec t0;
static struct timespec h_release; /* H's scheduled absolute release time */
static struct timespec h_done;
static atomic_int abort_run;
static atomic_int worker_failed;
static atomic_int h_completed;

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
    return (double)(b->tv_sec - a->tv_sec) * 1000.0 +
           (double)(b->tv_nsec - a->tv_nsec) / 1e6;
}

static int sleep_abs_monotonic(const struct timespec *wake)
{
    int rc;
    do {
        rc = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, wake, NULL);
    } while (rc == EINTR);
    return rc;
}

static int sleep_rel_ms(int ms)
{
    struct timespec wake;
    if (clock_gettime(CLOCK_MONOTONIC, &wake) != 0)
        return errno;
    add_ms(&wake, ms);
    return sleep_abs_monotonic(&wake);
}

static int busy_ms(int ms)
{
    struct timespec start, now;
    if (clock_gettime(CLOCK_THREAD_CPUTIME_ID, &start) != 0)
        return -1;
    do {
        if (clock_gettime(CLOCK_THREAD_CPUTIME_ID, &now) != 0)
            return -1;
    } while (diff_ms(&start, &now) < (double)ms);
    return 0;
}

static int sem_wait_retry(sem_t *sem)
{
    while (sem_wait(sem) != 0) {
        if (errno != EINTR)
            return errno;
    }
    return 0;
}

static void signal_waiters(void)
{
    for (int i = 0; i < 2; i++) {
        if (sem_post(&start_sem) != 0) {
            fprintf(stderr, "sem_post: %s\n", strerror(errno));
            atomic_store(&worker_failed, 1);
        }
    }
}

static void *worker(void *arg)
{
    const worker_arg_t *t = (const worker_arg_t *)arg;
    int rc;

    if (t->name == 'L') {
        rc = pthread_mutex_lock(&mutex);
        if (rc != 0) {
            fprintf(stderr, "L lock: %s\n", strerror(rc));
            atomic_store(&worker_failed, 1);
            atomic_store(&abort_run, 1);
            signal_waiters();
            return NULL;
        }

        if (clock_gettime(CLOCK_MONOTONIC, &t0) != 0) {
            fprintf(stderr, "L clock_gettime: %s\n", strerror(errno));
            atomic_store(&worker_failed, 1);
            atomic_store(&abort_run, 1);
            (void)pthread_mutex_unlock(&mutex);
            signal_waiters();
            return NULL;
        }

        /* Release H and M only after L holds the mutex and t0 is recorded. */
        signal_waiters();

        if (busy_ms(L_WORK_MS) != 0) {
            fprintf(stderr, "L busy_ms: %s\n", strerror(errno));
            atomic_store(&worker_failed, 1);
        }

        rc = pthread_mutex_unlock(&mutex);
        if (rc != 0) {
            fprintf(stderr, "L unlock: %s\n", strerror(rc));
            atomic_store(&worker_failed, 1);
        }
        return NULL;
    }

    rc = sem_wait_retry(&start_sem);
    if (rc != 0) {
        fprintf(stderr, "%c sem_wait: %s\n", t->name, strerror(rc));
        atomic_store(&worker_failed, 1);
        return NULL;
    }
    if (atomic_load(&abort_run) != 0)
        return NULL;

    struct timespec wake = t0;
    add_ms(&wake, (t->name == 'H') ? H_RELEASE_OFFSET_MS : M_RELEASE_OFFSET_MS);

    rc = sleep_abs_monotonic(&wake);
    if (rc != 0) {
        fprintf(stderr, "%c clock_nanosleep: %s\n", t->name, strerror(rc));
        atomic_store(&worker_failed, 1);
        return NULL;
    }

    if (t->name == 'H') {
        h_release = wake;
        rc = pthread_mutex_lock(&mutex);
        if (rc != 0) {
            fprintf(stderr, "H lock: %s\n", strerror(rc));
            atomic_store(&worker_failed, 1);
            return NULL;
        }
        if (busy_ms(H_WORK_MS) != 0) {
            fprintf(stderr, "H busy_ms: %s\n", strerror(errno));
            atomic_store(&worker_failed, 1);
            (void)pthread_mutex_unlock(&mutex);
            return NULL;
        }
        rc = pthread_mutex_unlock(&mutex);
        if (rc != 0) {
            fprintf(stderr, "H unlock: %s\n", strerror(rc));
            atomic_store(&worker_failed, 1);
            return NULL;
        }
        if (clock_gettime(CLOCK_MONOTONIC, &h_done) != 0) {
            fprintf(stderr, "H clock_gettime: %s\n", strerror(errno));
            atomic_store(&worker_failed, 1);
            return NULL;
        }
        atomic_store(&h_completed, 1);
    } else {
        if (busy_ms(M_WORK_MS) != 0) {
            fprintf(stderr, "M busy_ms: %s\n", strerror(errno));
            atomic_store(&worker_failed, 1);
        }
    }

    return NULL;
}

static int create_prio(pthread_t *th, const worker_arg_t *a)
{
    pthread_attr_t attr;
    struct sched_param sp;
    int rc;

    memset(&sp, 0, sizeof(sp));
    sp.sched_priority = a->prio;

    rc = pthread_attr_init(&attr);
    if (rc != 0)
        return rc;

    rc = pthread_attr_setinheritsched(&attr, PTHREAD_EXPLICIT_SCHED);
    if (rc == 0)
        rc = pthread_attr_setschedpolicy(&attr, SCHED_FIFO);
    if (rc == 0)
        rc = pthread_attr_setschedparam(&attr, &sp);
    if (rc == 0)
        rc = pthread_create(th, &attr, worker, (void *)a);

    (void)pthread_attr_destroy(&attr);
    return rc;
}

static int run_mode(const mode_spec_t *spec, double *out_response_ms)
{
    pthread_mutexattr_t mattr;
    pthread_t th[3];
    /* Spawn waiters H and M first so both block on start_sem before L locks. */
    const worker_arg_t args[3] = {
        {'H', PRIO_H},
        {'M', PRIO_M},
        {'L', PRIO_L},
    };
    int created = 0;
    int rc;
    int ret = 0;

    memset(&t0, 0, sizeof(t0));
    memset(&h_release, 0, sizeof(h_release));
    memset(&h_done, 0, sizeof(h_done));
    atomic_store(&abort_run, 0);
    atomic_store(&worker_failed, 0);
    atomic_store(&h_completed, 0);

    rc = pthread_mutexattr_init(&mattr);
    if (rc != 0) {
        fprintf(stderr, "mutexattr_init(%s): %s\n", spec->label, strerror(rc));
        return 1;
    }
    rc = pthread_mutexattr_setprotocol(&mattr, spec->protocol);
    if (rc != 0) {
        fprintf(stderr, "setprotocol(%s): %s\n", spec->label, strerror(rc));
        (void)pthread_mutexattr_destroy(&mattr);
        return 1;
    }
    if (spec->protocol == PTHREAD_PRIO_PROTECT) {
        rc = pthread_mutexattr_setprioceiling(&mattr, PRIO_H);
        if (rc != 0) {
            fprintf(stderr, "setprioceiling(%s): %s\n", spec->label, strerror(rc));
            (void)pthread_mutexattr_destroy(&mattr);
            return 1;
        }
    }
    rc = pthread_mutex_init(&mutex, &mattr);
    (void)pthread_mutexattr_destroy(&mattr);
    if (rc != 0) {
        fprintf(stderr, "mutex_init(%s): %s\n", spec->label, strerror(rc));
        return 1;
    }

    if (sem_init(&start_sem, 0, 0) != 0) {
        fprintf(stderr, "sem_init(%s): %s\n", spec->label, strerror(errno));
        (void)pthread_mutex_destroy(&mutex);
        return 1;
    }

    for (int i = 0; i < 3; i++) {
        rc = create_prio(&th[i], &args[i]);
        if (rc == EPERM) {
            fprintf(stderr,
                    "EPERM: SCHED_FIFO requires root or CAP_SYS_NICE.\n"
                    "Run: sudo ./posix/pi_demo --mode all\n"
                    "Or:  sudo setcap cap_sys_nice+ep posix/pi_demo && ./posix/pi_demo --mode all\n"
                    "Refusing to fall back to SCHED_OTHER.\n");
            ret = EXIT_EPERM;
            break;
        }
        if (rc != 0) {
            fprintf(stderr, "create %c (%s): %s\n", args[i].name, spec->label, strerror(rc));
            ret = 1;
            break;
        }
        created++;
    }

    if (ret != 0 && created > 0) {
        atomic_store(&abort_run, 1);
        for (int i = 0; i < created; i++)
            (void)sem_post(&start_sem);
    }

    for (int i = 0; i < created; i++) {
        rc = pthread_join(th[i], NULL);
        if (rc != 0) {
            fprintf(stderr, "join %c (%s): %s\n", args[i].name, spec->label, strerror(rc));
            if (ret == 0)
                ret = 1;
        }
    }

    if (sem_destroy(&start_sem) != 0) {
        fprintf(stderr, "sem_destroy(%s): %s\n", spec->label, strerror(errno));
        if (ret == 0)
            ret = 1;
    }
    rc = pthread_mutex_destroy(&mutex);
    if (rc != 0) {
        fprintf(stderr, "mutex_destroy(%s): %s\n", spec->label, strerror(rc));
        if (ret == 0)
            ret = 1;
    }

    if (ret != 0)
        return ret;
    if (atomic_load(&worker_failed) != 0 || atomic_load(&h_completed) == 0) {
        fprintf(stderr, "%s failed during worker execution.\n", spec->label);
        return 1;
    }

    *out_response_ms = diff_ms(&h_release, &h_done);
    return 0;
}

static void print_usage(FILE *stream, const char *prog)
{
    fprintf(stream,
            "Usage: %s [--mode all|none|inherit|protect] [--csv] [--help]\n",
            prog);
}

static bool parse_mode_value(const char *val, run_selection_t *out)
{
    if (strcmp(val, "all") == 0) {
        *out = MODE_ALL;
        return true;
    }
    if (strcmp(val, "none") == 0) {
        *out = MODE_NONE;
        return true;
    }
    if (strcmp(val, "inherit") == 0) {
        *out = MODE_INHERIT;
        return true;
    }
    if (strcmp(val, "protect") == 0) {
        *out = MODE_PROTECT;
        return true;
    }
    return false;
}

static int pin_single_core(void)
{
    cpu_set_t allowed;
    cpu_set_t pinned;
    int chosen_cpu = 0;

    CPU_ZERO(&allowed);
    if (sched_getaffinity(0, sizeof(allowed), &allowed) == 0) {
        if (!CPU_ISSET(0, &allowed)) {
            bool found = false;
            for (int cpu = 0; cpu < CPU_SETSIZE; cpu++) {
                if (CPU_ISSET(cpu, &allowed)) {
                    chosen_cpu = cpu;
                    found = true;
                    break;
                }
            }
            if (!found) {
                fprintf(stderr, "sched_getaffinity: no runnable CPU in affinity mask\n");
                return -1;
            }
        }
    }

    CPU_ZERO(&pinned);
    CPU_SET(chosen_cpu, &pinned);
    if (sched_setaffinity(0, sizeof(pinned), &pinned) != 0) {
        perror("sched_setaffinity");
        return -1;
    }
    return 0;
}

int main(int argc, char **argv)
{
    run_selection_t selection = MODE_ALL;
    bool csv_output = false;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--csv") == 0) {
            csv_output = true;
        } else if (strcmp(argv[i], "--mode") == 0) {
            if (i + 1 >= argc || !parse_mode_value(argv[i + 1], &selection)) {
                fprintf(stderr, "Invalid or missing value for --mode.\n");
                print_usage(stderr, argv[0]);
                return 2;
            }
            i++;
        } else if (strncmp(argv[i], "--mode=", 7) == 0) {
            if (!parse_mode_value(argv[i] + 7, &selection)) {
                fprintf(stderr, "Invalid value for --mode: %s\n", argv[i] + 7);
                print_usage(stderr, argv[0]);
                return 2;
            }
        } else if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            print_usage(stdout, argv[0]);
            return 0;
        } else {
            fprintf(stderr, "Unknown argument: %s\n", argv[i]);
            print_usage(stderr, argv[0]);
            return 2;
        }
    }

    if (pin_single_core() != 0)
        return 1;

    /* Lock memory only when RLIMIT_MEMLOCK is unlimited; calling MCL_FUTURE
     * under a finite limit (e.g. Ubuntu's default 8 MB) makes pthread_create's
     * default 8 MB stack mmap fail with EAGAIN before SCHED_FIFO runs. */
    struct rlimit memlock_limit;
    if (getrlimit(RLIMIT_MEMLOCK, &memlock_limit) == 0 &&
        memlock_limit.rlim_cur == RLIM_INFINITY) {
        if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0)
            perror("mlockall (continuing)");
    }

    const mode_spec_t specs[3] = {
        {PTHREAD_PRIO_NONE, "PTHREAD_PRIO_NONE", "inversion_gt_120ms"},
        {PTHREAD_PRIO_INHERIT, "PTHREAD_PRIO_INHERIT", "bounded_lt_60ms"},
        {PTHREAD_PRIO_PROTECT, "PTHREAD_PRIO_PROTECT", "bounded_lt_60ms"},
    };

    int start_idx = 0;
    int end_idx = 3;
    if (selection == MODE_NONE) {
        start_idx = 0;
        end_idx = 1;
    } else if (selection == MODE_INHERIT) {
        start_idx = 1;
        end_idx = 2;
    } else if (selection == MODE_PROTECT) {
        start_idx = 2;
        end_idx = 3;
    }

    double responses[3] = {0.0, 0.0, 0.0};
    for (int i = start_idx; i < end_idx; i++) {
        int rc = run_mode(&specs[i], &responses[i]);
        if (rc != 0)
            return rc;
        if (i + 1 < end_idx) {
            int gap_rc = sleep_rel_ms(MODE_GAP_MS);
            if (gap_rc != 0) {
                fprintf(stderr, "inter-mode sleep: %s\n", strerror(gap_rc));
                return 1;
            }
        }
    }

    if (csv_output) {
        printf("protocol,h_response_ms,expected_pattern\n");
        for (int i = start_idx; i < end_idx; i++) {
            printf("%s,%.2f,%s\n",
                   specs[i].label, responses[i], specs[i].expected_pattern);
        }
    } else {
        printf("Pathfinder cross-check (single core, SCHED_FIFO)\n");
        printf("Timings: L=%dms lock, H=+%dms (%dms), M=+%dms (%dms)\n",
               L_WORK_MS, H_RELEASE_OFFSET_MS, H_WORK_MS,
               M_RELEASE_OFFSET_MS, M_WORK_MS);
        printf("Expect PTHREAD_PRIO_NONE >120ms (~155ms), INHERIT/PROTECT <60ms (~35ms)\n");
        printf("(VM/hypervisor jitter can still shift absolute timings)\n\n");
        for (int i = start_idx; i < end_idx; i++) {
            printf("%-22s  H response = %6.2f ms  (%s)\n",
                   specs[i].label, responses[i], specs[i].expected_pattern);
        }
    }

    return 0;
}
