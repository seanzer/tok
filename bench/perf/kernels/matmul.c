#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
// Rows are separate allocations, as in the Tok program.
static double** make(int64_t n, int64_t seed) {
  double** m = malloc((size_t)n * sizeof *m);
  for (int64_t i = 0; i < n; i++) {
    m[i] = malloc((size_t)n * sizeof **m);
    for (int64_t j = 0; j < n; j++) m[i][j] = (double)((i * seed + j) % 10);
  }
  return m;
}
static void drop(double** m, int64_t n) { for (int64_t i = 0; i < n; i++) free(m[i]); free(m); }
static double** mul(double** a, double** b, int64_t n) {
  double** c = malloc((size_t)n * sizeof *c);
  for (int64_t i = 0; i < n; i++) {
    c[i] = malloc((size_t)n * sizeof **c);
    for (int64_t j = 0; j < n; j++) {
      double s = 0.0;
      for (int64_t k = 0; k < n; k++) s = s + a[i][k] * b[k][j];
      c[i][j] = s;
    }
  }
  return c;
}
int main(int argc, char** argv) {
  int64_t n = argc > 1 ? atoll(argv[1]) : 300, rounds = argc > 2 ? atoll(argv[2]) : 2;
  double** a = make(n, 3);
  double** b = make(n, 7);
  double total = 0.0;
  for (int64_t r = 0; r < rounds; r++) {
    double** c = mul(a, b, n);
    total = total + c[0][0] + c[n / 2][n / 3] + c[n - 1][n - 1];
    drop(c, n);
  }
  drop(a, n); drop(b, n);
  printf("%lld\n", (long long)total);
  return 0;
}
