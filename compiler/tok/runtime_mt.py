"""The threads runtime. It is part of a generated program only when the program uses `par for`, `spawn`, a channel
or an atomic; every other program is single-threaded C with no pthread dependency.

What is here:
  - a pool of worker threads, each with its own queue of jobs. A worker takes from the back of its own queue and,
    when that is empty, steals from the front of another one. TOK_THREADS sets how many threads run a `par for`
    (the calling thread included); the default is the number of cores.
  - `par for`: the iterations are cut into chunks (at most 64, the cut depends only on the iteration count), helper
    jobs are published, and the caller and the helpers claim chunks from a shared atomic counter.
  - tasks (`spawn`): a job with a result slot and a reference count that is atomic.
  - channels: a bounded ring buffer under one mutex with two condition variables.
  - atomics: one C11 atomic cell with an atomic reference count.

What is not here: green threads. A task runs on an OS thread from the pool, and a task that blocks (a full or empty
channel, `wait()` on a task that is running) parks that OS thread. So that queued work keeps running, the pool starts
a replacement thread when a thread is about to block while jobs are waiting (up to 256 threads).

Reference counts of strings, lists and dicts stay plain (non-atomic). See README, "Concurrency".
"""

PRELUDE = r'''#define TOK_MT 1
#define _POSIX_C_SOURCE 200809L
#include <pthread.h>
#include <stdatomic.h>
#include <unistd.h>
'''

RUNTIME = r'''
// ---- threads: the pool
#define TOK_MAXW 257
typedef struct tok_job tok_job;
struct tok_job { void (*run)(tok_job*); };
typedef struct { pthread_mutex_t mu; tok_job** buf; int64_t head, len, cap; } tok_deque;
static struct {
  pthread_mutex_t mu;
  pthread_cond_t cv, done_cv;
  int target, nworkers, nidle, nblocked;
  bool stop;
  bool used[TOK_MAXW];
  atomic_int nslots;
  _Atomic int64_t pending, tasks;
  tok_deque dq[TOK_MAXW];  // 0: jobs from threads that are not pool workers
} tok_pool;
static int tok_nthreads = 1;
static _Thread_local int tok_wid = 0;     // this thread's queue; 0 for a thread that is not a pool worker
static _Thread_local int tok_in_par = 0;  // > 0 while this thread runs a chunk of a par for

static void tok_dq_push(tok_deque* d, tok_job* j) {
  pthread_mutex_lock(&d->mu);
  if (d->len == d->cap) {
    int64_t cap = d->cap ? d->cap * 2 : 16;
    tok_job** b = (tok_job**)malloc((size_t)cap * sizeof(tok_job*));
    if (!b) tok_oom();
    for (int64_t i = 0; i < d->len; i++) b[i] = d->buf[(d->head + i) % d->cap];
    free(d->buf);
    d->buf = b; d->head = 0; d->cap = cap;
  }
  d->buf[(d->head + d->len) % d->cap] = j;
  d->len++;
  pthread_mutex_unlock(&d->mu);
}
static tok_job* tok_dq_pop(tok_deque* d, bool back) {
  tok_job* j = NULL;
  pthread_mutex_lock(&d->mu);
  if (d->len > 0) {
    if (back) j = d->buf[(d->head + d->len - 1) % d->cap];
    else { j = d->buf[d->head]; d->head = (d->head + 1) % d->cap; }
    d->len--;
  }
  pthread_mutex_unlock(&d->mu);
  return j;
}
// the next job for this thread: its own newest one, or the oldest one of another queue (a steal)
static tok_job* tok_take(void) {
  if (atomic_load(&tok_pool.pending) == 0) return NULL;
  tok_job* j = tok_dq_pop(&tok_pool.dq[tok_wid], tok_wid != 0);
  int n = atomic_load(&tok_pool.nslots);
  for (int k = 1; !j && k < n; k++) j = tok_dq_pop(&tok_pool.dq[(tok_wid + k) % n], false);
  if (j) atomic_fetch_sub(&tok_pool.pending, 1);
  return j;
}
static void* tok_worker(void* arg) {
  tok_wid = (int)(intptr_t)arg;
  for (;;) {
    tok_job* j = tok_take();
    if (j) { j->run(j); continue; }
    pthread_mutex_lock(&tok_pool.mu);
    if (atomic_load(&tok_pool.pending) > 0) { pthread_mutex_unlock(&tok_pool.mu); continue; }
    if (tok_pool.stop || tok_pool.nworkers - tok_pool.nblocked > tok_pool.target) {
      tok_pool.used[tok_wid] = false;
      tok_pool.nworkers--;
      if (tok_pool.nworkers == 0) pthread_cond_broadcast(&tok_pool.done_cv);
      pthread_mutex_unlock(&tok_pool.mu);
      return NULL;
    }
    tok_pool.nidle++;
    pthread_cond_wait(&tok_pool.cv, &tok_pool.mu);
    tok_pool.nidle--;
    pthread_mutex_unlock(&tok_pool.mu);
  }
}
// tok_pool.mu is held
static void tok_start_thread(void) {
  int id = 0;
  for (int i = 1; i < TOK_MAXW; i++) if (!tok_pool.used[i]) { id = i; break; }
  if (!id) return;
  pthread_attr_t at;
  pthread_attr_init(&at);
  pthread_attr_setdetachstate(&at, PTHREAD_CREATE_DETACHED);
  tok_pool.used[id] = true;
  tok_pool.nworkers++;
  if (id >= atomic_load(&tok_pool.nslots)) atomic_store(&tok_pool.nslots, id + 1);
  pthread_t th;
  if (pthread_create(&th, &at, tok_worker, (void*)(intptr_t)id) != 0) { tok_pool.used[id] = false; tok_pool.nworkers--; }
  pthread_attr_destroy(&at);
}
static void tok_push(tok_job* j) {
  tok_dq_push(&tok_pool.dq[tok_wid], j);
  atomic_fetch_add(&tok_pool.pending, 1);
  pthread_mutex_lock(&tok_pool.mu);
  if (tok_pool.nidle > 0) pthread_cond_signal(&tok_pool.cv);
  else if (tok_pool.nworkers - tok_pool.nblocked < tok_pool.target) tok_start_thread();
  else pthread_cond_broadcast(&tok_pool.done_cv);  // main, if it is waiting for the end of the program, can take it
  pthread_mutex_unlock(&tok_pool.mu);
}
// Around every wait that parks this OS thread. If jobs are queued and no worker is free to take them, one more
// thread is started, so a pool whose threads are all parked on channels still runs the task that will wake them.
static void tok_block_begin(void) {
  pthread_mutex_lock(&tok_pool.mu);
  if (tok_wid) tok_pool.nblocked++;
  if (atomic_load(&tok_pool.pending) > 0) {
    if (tok_pool.nidle > 0) pthread_cond_signal(&tok_pool.cv); else tok_start_thread();
  }
  pthread_mutex_unlock(&tok_pool.mu);
}
static void tok_block_end(void) {
  if (!tok_wid) return;
  pthread_mutex_lock(&tok_pool.mu);
  tok_pool.nblocked--;
  pthread_mutex_unlock(&tok_pool.mu);
}
static void tok_mt_init(void) {
  long n = sysconf(_SC_NPROCESSORS_ONLN);
  const char* e = getenv("TOK_THREADS");
  if (e && *e) {
    char* end;
    long v = strtol(e, &end, 10);
    if (*end == 0 && v >= 1) n = v;
  }
  if (n < 1) n = 1;
  if (n > TOK_MAXW - 1) n = TOK_MAXW - 1;
  tok_nthreads = (int)n;
  tok_pool.target = tok_nthreads - 1;
  atomic_store(&tok_pool.nslots, 1);
  pthread_mutex_init(&tok_pool.mu, NULL);
  pthread_cond_init(&tok_pool.cv, NULL);
  pthread_cond_init(&tok_pool.done_cv, NULL);
  for (int i = 0; i < TOK_MAXW; i++) pthread_mutex_init(&tok_pool.dq[i].mu, NULL);
}
// The end of the program: main helps run what is queued, waits for every task to finish, then stops the workers.
static void tok_mt_finish(void) {
  for (;;) {
    tok_job* j = tok_take();
    if (j) { j->run(j); continue; }
    pthread_mutex_lock(&tok_pool.mu);
    while (atomic_load(&tok_pool.tasks) > 0 && atomic_load(&tok_pool.pending) == 0) pthread_cond_wait(&tok_pool.done_cv, &tok_pool.mu);
    bool done = atomic_load(&tok_pool.tasks) == 0 && atomic_load(&tok_pool.pending) == 0;
    pthread_mutex_unlock(&tok_pool.mu);
    if (done) break;
  }
  pthread_mutex_lock(&tok_pool.mu);
  tok_pool.stop = true;
  pthread_cond_broadcast(&tok_pool.cv);
  while (tok_pool.nworkers > 0) pthread_cond_wait(&tok_pool.done_cv, &tok_pool.mu);
  pthread_mutex_unlock(&tok_pool.mu);
  for (int i = 0; i < TOK_MAXW; i++) { free(tok_pool.dq[i].buf); tok_pool.dq[i].buf = NULL; }
}

// ---- par for
typedef struct { int64_t n, csize, nchunks; bool par; } tok_plan;
typedef void (*tok_pfn)(void* cx, int64_t lo, int64_t hi, int64_t chunk);
typedef struct {
  tok_job job;
  tok_pfn fn;
  void* cx;
  int64_t n, csize, nchunks;
  _Atomic int64_t next, done;
  atomic_int refs;
  pthread_mutex_t mu;
  pthread_cond_t cv;
} tok_pf;
// How n iterations are cut up. The cut depends only on n, never on the number of threads, so a reduction gives
// the same result however many threads run it. `par` is false when the loop runs on the calling thread alone:
// one thread, one chunk, or a par for inside a par for.
// The number of iterations of `par for i in lo..hi`, given hi - lo.
TOK_UNUSED static int64_t tok_par_count(uint64_t span, const char* loc) {
  if (span >= (uint64_t)INT64_MAX) tok_panic(loc, "this range has too many values for a par for");
  return (int64_t)span + 1;
}
TOK_UNUSED static tok_plan tok_par_plan(int64_t n) {
  tok_plan p;
  p.n = n;
  p.csize = n > 0 ? (n + 63) / 64 : 1;
  p.nchunks = n > 0 ? (n + p.csize - 1) / p.csize : 0;
  p.par = tok_in_par == 0 && tok_nthreads > 1 && p.nchunks > 1;
  return p;
}
static void tok_pf_work(tok_pf* p) {
  tok_in_par++;
  for (;;) {
    int64_t c = atomic_fetch_add(&p->next, 1);
    if (c >= p->nchunks) break;
    int64_t lo = c * p->csize, hi = lo + p->csize < p->n ? lo + p->csize : p->n;
    p->fn(p->cx, lo, hi, c);
    if (atomic_fetch_add(&p->done, 1) + 1 == p->nchunks) {
      pthread_mutex_lock(&p->mu);
      pthread_cond_broadcast(&p->cv);
      pthread_mutex_unlock(&p->mu);
    }
  }
  tok_in_par--;
}
static void tok_pf_unref(tok_pf* p) {
  if (atomic_fetch_sub(&p->refs, 1) == 1) {
    pthread_mutex_destroy(&p->mu);
    pthread_cond_destroy(&p->cv);
    tok_free(p);
  }
}
static void tok_pf_help(tok_job* j) { tok_pf* p = (tok_pf*)(void*)j; tok_pf_work(p); tok_pf_unref(p); }
TOK_UNUSED static void tok_par_run(const tok_plan* pl, tok_pfn fn, void* cx) {
  if (!pl->par) {
    for (int64_t c = 0; c < pl->nchunks; c++) {
      int64_t lo = c * pl->csize, hi = lo + pl->csize < pl->n ? lo + pl->csize : pl->n;
      fn(cx, lo, hi, c);
    }
    return;
  }
  tok_pf* p = (tok_pf*)tok_alloc(sizeof(tok_pf));
  int helpers = tok_nthreads - 1 < pl->nchunks - 1 ? tok_nthreads - 1 : (int)(pl->nchunks - 1);
  p->job.run = tok_pf_help;
  p->fn = fn; p->cx = cx; p->n = pl->n; p->csize = pl->csize; p->nchunks = pl->nchunks;
  atomic_init(&p->next, 0);
  atomic_init(&p->done, 0);
  atomic_init(&p->refs, 1 + helpers);
  pthread_mutex_init(&p->mu, NULL);
  pthread_cond_init(&p->cv, NULL);
  for (int i = 0; i < helpers; i++) tok_push(&p->job);
  tok_pf_work(p);
  pthread_mutex_lock(&p->mu);
  while (atomic_load(&p->done) < p->nchunks) pthread_cond_wait(&p->cv, &p->mu);
  pthread_mutex_unlock(&p->mu);
  tok_pf_unref(p);
}

// ---- tasks. state: 0 queued, 1 running, 2 finished, 3 finished and its value taken by wait()
typedef struct tok_task {
  tok_job job;
  _Atomic int64_t rc;
  atomic_int state;
  pthread_mutex_t mu;
  pthread_cond_t cv;
  void (*fn)(void* env);
  void (*drop)(void* env);  // releases a result that nobody took
  void* env;                // the arguments and the result slot
} tok_task;
TOK_UNUSED static inline tok_task* tok_task_ret(tok_task* t) { if (t) atomic_fetch_add(&t->rc, 1); return t; }
TOK_UNUSED static inline void tok_task_retain(tok_task* t) { if (t) atomic_fetch_add(&t->rc, 1); }
TOK_UNUSED static void tok_task_release(tok_task* t) {
  if (!t || atomic_fetch_sub(&t->rc, 1) != 1) return;
  if (atomic_load(&t->state) == 2 && t->drop) t->drop(t->env);
  pthread_mutex_destroy(&t->mu);
  pthread_cond_destroy(&t->cv);
  tok_free(t->env);
  tok_free(t);
}
static void tok_task_exec(tok_task* t) {
  int saved = tok_in_par;
  tok_in_par = 0;
  t->fn(t->env);
  tok_in_par = saved;
  pthread_mutex_lock(&t->mu);
  atomic_store(&t->state, 2);
  pthread_cond_broadcast(&t->cv);
  pthread_mutex_unlock(&t->mu);
  if (atomic_fetch_sub(&tok_pool.tasks, 1) == 1) {
    pthread_mutex_lock(&tok_pool.mu);
    pthread_cond_broadcast(&tok_pool.done_cv);
    pthread_mutex_unlock(&tok_pool.mu);
  }
}
static void tok_task_run(tok_job* j) {
  tok_task* t = (tok_task*)(void*)j;
  int q = 0;
  if (atomic_compare_exchange_strong(&t->state, &q, 1)) tok_task_exec(t);
  tok_task_release(t);  // the queue's reference
}
TOK_UNUSED static tok_task* tok_spawn(void (*fn)(void*), void (*drop)(void*), void* env) {
  tok_task* t = (tok_task*)tok_alloc(sizeof(tok_task));
  t->job.run = tok_task_run;
  atomic_init(&t->rc, 2);  // the handle and the queue
  atomic_init(&t->state, 0);
  pthread_mutex_init(&t->mu, NULL);
  pthread_cond_init(&t->cv, NULL);
  t->fn = fn; t->drop = drop; t->env = env;
  atomic_fetch_add(&tok_pool.tasks, 1);
  tok_push(&t->job);
  return t;
}
// Wait for the task and take its value. A task that no thread has started yet runs right here, on the waiting
// thread; otherwise this thread parks until the task finishes.
TOK_UNUSED static void tok_task_wait(tok_task* t, const char* loc) {
  int q = 0;
  if (atomic_compare_exchange_strong(&t->state, &q, 1)) {
    tok_task_exec(t);
  } else {
    pthread_mutex_lock(&t->mu);
    if (atomic_load(&t->state) < 2) {
      tok_block_begin();
      while (atomic_load(&t->state) < 2) pthread_cond_wait(&t->cv, &t->mu);
      tok_block_end();
    }
    pthread_mutex_unlock(&t->mu);
  }
  q = 2;
  if (!atomic_compare_exchange_strong(&t->state, &q, 3))
    tok_panic(loc, "wait() was already called on this task: a task's value can be taken once");
}

// ---- channels: a bounded queue of values. Whatever is inside belongs to the channel alone.
typedef struct tok_chan {
  _Atomic int64_t rc;
  pthread_mutex_t mu;
  pthread_cond_t can_send, can_recv;
  char* buf;
  size_t esz;
  int64_t cap, len, head;
  bool closed;
  void (*drop)(void* item);
} tok_chan;
TOK_UNUSED static tok_chan* tok_chan_new(int64_t cap, size_t esz, void (*drop)(void*), const char* loc) {
  if (cap < 1) tok_panic(loc, "a channel needs room for at least 1 value, found %lld", (long long)cap);
  tok_chan* c = (tok_chan*)tok_alloc(sizeof(tok_chan));
  atomic_init(&c->rc, 1);
  pthread_mutex_init(&c->mu, NULL);
  pthread_cond_init(&c->can_send, NULL);
  pthread_cond_init(&c->can_recv, NULL);
  c->buf = (char*)tok_alloc((size_t)cap * esz);
  c->esz = esz; c->cap = cap; c->len = 0; c->head = 0; c->closed = false; c->drop = drop;
  return c;
}
TOK_UNUSED static inline tok_chan* tok_chan_ret(tok_chan* c) { if (c) atomic_fetch_add(&c->rc, 1); return c; }
TOK_UNUSED static inline void tok_chan_retain(tok_chan* c) { if (c) atomic_fetch_add(&c->rc, 1); }
TOK_UNUSED static void tok_chan_release(tok_chan* c) {
  if (!c || atomic_fetch_sub(&c->rc, 1) != 1) return;
  if (c->drop) for (int64_t i = 0; i < c->len; i++) c->drop(c->buf + (size_t)((c->head + i) % c->cap) * c->esz);
  pthread_mutex_destroy(&c->mu);
  pthread_cond_destroy(&c->can_send);
  pthread_cond_destroy(&c->can_recv);
  tok_free(c->buf);
  tok_free(c);
}
// blocks while the channel is full
TOK_UNUSED static void tok_chan_send(tok_chan* c, const void* v, const char* loc) {
  pthread_mutex_lock(&c->mu);
  if (c->len == c->cap && !c->closed) {
    tok_block_begin();
    while (c->len == c->cap && !c->closed) pthread_cond_wait(&c->can_send, &c->mu);
    tok_block_end();
  }
  if (c->closed) tok_panic(loc, "send on a closed channel");
  memcpy(c->buf + (size_t)((c->head + c->len) % c->cap) * c->esz, v, c->esz);
  c->len++;
  pthread_cond_signal(&c->can_recv);
  pthread_mutex_unlock(&c->mu);
}
// blocks while the channel is empty and open; false once it is closed and empty
TOK_UNUSED static bool tok_chan_recv(tok_chan* c, void* out) {
  pthread_mutex_lock(&c->mu);
  if (c->len == 0 && !c->closed) {
    tok_block_begin();
    while (c->len == 0 && !c->closed) pthread_cond_wait(&c->can_recv, &c->mu);
    tok_block_end();
  }
  bool got = c->len > 0;
  if (got) {
    memcpy(out, c->buf + (size_t)c->head * c->esz, c->esz);
    c->head = (c->head + 1) % c->cap;
    c->len--;
    pthread_cond_signal(&c->can_send);
  }
  pthread_mutex_unlock(&c->mu);
  return got;
}
TOK_UNUSED static void tok_chan_close(tok_chan* c) {
  pthread_mutex_lock(&c->mu);
  c->closed = true;
  pthread_cond_broadcast(&c->can_send);
  pthread_cond_broadcast(&c->can_recv);
  pthread_mutex_unlock(&c->mu);
}

// ---- atomics: the one kind of value that tasks share and change. Every operation is sequentially consistent.
typedef struct { _Atomic int64_t rc; _Atomic int64_t v; } tok_atom;
TOK_UNUSED static tok_atom* tok_atom_new(int64_t v) {
  tok_atom* a = (tok_atom*)tok_alloc(sizeof(tok_atom));
  atomic_init(&a->rc, 1);
  atomic_init(&a->v, v);
  return a;
}
TOK_UNUSED static inline tok_atom* tok_atom_ret(tok_atom* a) { if (a) atomic_fetch_add(&a->rc, 1); return a; }
TOK_UNUSED static inline void tok_atom_retain(tok_atom* a) { if (a) atomic_fetch_add(&a->rc, 1); }
TOK_UNUSED static inline void tok_atom_release(tok_atom* a) { if (a && atomic_fetch_sub(&a->rc, 1) == 1) tok_free(a); }
TOK_UNUSED static inline bool tok_atom_cas(tok_atom* a, int64_t expected, int64_t v) {
  return atomic_compare_exchange_strong(&a->v, &expected, v);
}

// ---- values that cross between threads
// While a par for runs, the buffers its body can read are pinned: the count is moved out of the counted range, so
// the retain and release of every thread become no-ops on them and no thread writes a count that another reads.
// The caller holds its references for the whole loop, and puts the counts back when the loop is done.
TOK_UNUSED static void tok_pin_str(const char* s) { if (s) { tok_shdr* h = TOK_SH(s); if (h->rc > 0) h->rc -= TOK_PIN; } }
TOK_UNUSED static void tok_unpin_str(const char* s) { if (s) { tok_shdr* h = TOK_SH(s); if (h->rc < -1) h->rc += TOK_PIN; } }
// A value that leaves its thread (into a task, a channel, or the result of a par for) must share no buffer with
// anything that stays behind: a buffer with one owner moves as it is, any other is copied.
TOK_UNUSED static const char* tok_iso_str(const char* s) {
  if (!s) return s;
  int64_t rc = TOK_SH(s)->rc;
  if (rc == 1 || rc == -1) return s;
  const char* r = tok_sfrom(s, tok_slen(s));
  tok_srelease(s);
  return r;
}
TOK_UNUSED static tok_error tok_iso_errv(tok_error e) { e.msg = tok_iso_str(e.msg); return e; }
'''
