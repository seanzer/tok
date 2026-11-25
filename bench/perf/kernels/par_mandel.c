// OpenMP reference.
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
static int64_t row(int64_t y, int64_t size, int64_t limit) {
  double ci = 2.0 * (double)y / (double)size - 1.0;
  int64_t count = 0;
  for (int64_t x = 0; x < size; x++) {
    double cr = 3.0 * (double)x / (double)size - 2.0, zr = 0.0, zi = 0.0;
    int64_t k = 0;
    while (k < limit && zr * zr + zi * zi <= 4.0) {
      double nr = zr * zr - zi * zi + cr, ni = 2.0 * zr * zi + ci;
      zr = nr; zi = ni; k++;
    }
    count += k;
  }
  return count;
}
int main(int argc, char** argv) {
  int64_t size = argc > 1 ? atoll(argv[1]) : 600, limit = argc > 2 ? atoll(argv[2]) : 300;
  int64_t* rows = calloc((size_t)size, sizeof *rows);
  #pragma omp parallel for schedule(dynamic, 1)
  for (int64_t y = 0; y < size; y++) rows[y] = row(y, size, limit);
  int64_t sum = 0;
  for (int64_t y = 0; y < size; y++) sum += rows[y];
  free(rows);
  printf("%lld\n", (long long)sum);
  return 0;
}
