#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
typedef struct { double x, y, vx, vy; char* name; } body;
// Each step builds a new array, as the Tok program does; the names are shared, not copied.
int main(int argc, char** argv) {
  int64_t n = argc > 1 ? atoll(argv[1]) : 50000, steps = argc > 2 ? atoll(argv[2]) : 40;
  char* names[100];
  for (int i = 0; i < 100; i++) { names[i] = malloc(8); snprintf(names[i], 8, "b%d", i); }
  body* bs = malloc((size_t)n * sizeof *bs);
  for (int64_t i = 1; i <= n; i++) bs[i - 1] = (body){0.0, 0.0, (double)(i % 17), (double)(i % 5), names[i % 100]};
  double dt = 0.01;
  for (int64_t s = 0; s < steps; s++) {
    body* nb = malloc((size_t)n * sizeof *nb);
    for (int64_t i = 0; i < n; i++) {
      body b = bs[i];
      nb[i] = (body){b.x + b.vx * dt, b.y + b.vy * dt, b.vx * 0.999, b.vy - 9.8 * dt, b.name};
    }
    free(bs);
    bs = nb;
  }
  double sum = 0.0;
  int64_t chars = 0;
  for (int64_t i = 0; i < n; i++) { sum = sum + bs[i].x + bs[i].y; chars += (int64_t)strlen(bs[i].name); }
  printf("%lld\n%lld\n", (long long)sum, (long long)chars);
  free(bs);
  for (int i = 0; i < 100; i++) free(names[i]);
  return 0;
}
