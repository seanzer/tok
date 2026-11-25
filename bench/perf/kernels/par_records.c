// OpenMP reference.
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
typedef struct { double x, y, vx, vy; int64_t id; } Body;
int main(int argc, char** argv) {
  int64_t n = argc > 1 ? atoll(argv[1]) : 400000, steps = argc > 2 ? atoll(argv[2]) : 40;
  Body* bs = malloc((size_t)n * sizeof *bs);
  for (int64_t i = 1; i <= n; i++) bs[i - 1] = (Body){0.0, 0.0, (double)(i % 17), (double)(i % 5), i};
  double dt = 0.01;
  for (int64_t s = 1; s <= steps; s++) {
    Body* nx = malloc((size_t)n * sizeof *nx);
    #pragma omp parallel for schedule(static)
    for (int64_t i = 0; i < n; i++) {
      Body b = bs[i];
      nx[i] = (Body){b.x + b.vx * dt, b.y + b.vy * dt, b.vx * 0.999, b.vy - 9.8 * dt, b.id};
    }
    free(bs);
    bs = nx;
  }
  double sum = 0.0;
  int64_t ids = 0;
  for (int64_t i = 0; i < n; i++) { sum = sum + bs[i].x + bs[i].y; ids += bs[i].id % 7; }
  free(bs);
  printf("%lld\n%lld\n", (long long)sum, (long long)ids);
  return 0;
}
