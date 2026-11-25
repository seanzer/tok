// Runs a command and reports its wall time and peak resident memory.
//   timeit STATS_FILE command [args...]
// Writes "<seconds> <peak KiB> <exit code>" to STATS_FILE. The command's own output is left alone.
// A small launcher is used instead of measuring from Python because a child forked from Python starts
// with the Python process's memory counted in its peak.
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <sys/resource.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

int main(int argc, char** argv) {
  if (argc < 3) { fputs("usage: timeit STATS_FILE command [args...]\n", stderr); return 64; }
  struct timespec a, b;
  clock_gettime(CLOCK_MONOTONIC, &a);
  pid_t pid = fork();
  if (pid < 0) { perror("fork"); return 70; }
  if (pid == 0) {
    execvp(argv[2], argv + 2);
    perror(argv[2]);
    _exit(127);
  }
  int status = 0;
  if (waitpid(pid, &status, 0) < 0) { perror("waitpid"); return 70; }
  clock_gettime(CLOCK_MONOTONIC, &b);
  struct rusage ru;
  getrusage(RUSAGE_CHILDREN, &ru);
  double secs = (double)(b.tv_sec - a.tv_sec) + (double)(b.tv_nsec - a.tv_nsec) / 1e9;
  int code = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
  FILE* f = fopen(argv[1], "w");
  if (!f) { perror(argv[1]); return 70; }
  fprintf(f, "%.6f %ld %d\n", secs, ru.ru_maxrss, code);
  fclose(f);
  return code;
}
