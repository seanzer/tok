#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
static double mean(const double* xs, int64_t n) {
  double s = 0;
  for (int64_t i = 0; i < n; i++) s += xs[i];
  return s / (double)n;
}
static double* normalize(const double* xs, int64_t n) {
  double m = mean(xs, n);
  double* d = malloc((size_t)n * sizeof *d);
  for (int64_t i = 0; i < n; i++) d[i] = (xs[i] - m) * (xs[i] - m);
  double s = sqrt(mean(d, n));
  free(d);
  double* out = malloc((size_t)n * sizeof *out);
  for (int64_t i = 0; i < n; i++) out[i] = (xs[i] - m) / s;
  return out;
}
int main(int argc, char** argv) {
  int64_t n = argc > 1 ? atoll(argv[1]) : 1000000, rounds = argc > 2 ? atoll(argv[2]) : 8;
  double* xs = malloc((size_t)n * sizeof *xs);
  for (int64_t i = 1; i <= n; i++) xs[i - 1] = (double)(i % 1000) * 0.5;
  double total = 0.0;
  for (int64_t r = 1; r <= rounds; r++) {
    double* ys = normalize(xs, n);
    double mx = ys[0];
    for (int64_t i = 1; i < n; i++) if (ys[i] > mx) mx = ys[i];
    total = total + ys[r] + ys[n / 2] + mx;
    free(ys);
  }
  free(xs);
  printf("%lld\n", (long long)(total * 1000.0));
  return 0;
}
