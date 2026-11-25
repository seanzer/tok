// OpenMP reference. The sums use the same 64 fixed chunks, combined in order, that Tok's par for uses, so the
// checksum is the same for every thread count.
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#define CHUNKS 64
static double chunked(const double* xs, int64_t n, double m, int sq) {
  int64_t cs = (n + CHUNKS - 1) / CHUNKS, nc = (n + cs - 1) / cs;
  double part[CHUNKS];
  #pragma omp parallel for schedule(dynamic, 1)
  for (int64_t c = 0; c < nc; c++) {
    int64_t lo = c * cs, hi = lo + cs < n ? lo + cs : n;
    double s = 0;
    if (sq) for (int64_t i = lo; i < hi; i++) s += (xs[i] - m) * (xs[i] - m);
    else for (int64_t i = lo; i < hi; i++) s += xs[i];
    part[c] = s;
  }
  double s = 0;
  for (int64_t c = 0; c < nc; c++) s += part[c];
  return s;
}
static double* normalize(const double* xs, int64_t n) {
  double m = chunked(xs, n, 0, 0) / (double)n;
  double s = sqrt(chunked(xs, n, m, 1) / (double)n);
  double* out = malloc((size_t)n * sizeof *out);
  #pragma omp parallel for schedule(static)
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
