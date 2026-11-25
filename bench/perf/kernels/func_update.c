#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
// What a C programmer writes: the vector is grown in place.
typedef struct { int64_t* d; size_t len, cap; } vec;
static void with_item(vec* v, int64_t x) {
  if (v->len == v->cap) { v->cap = v->cap ? v->cap * 2 : 4; v->d = realloc(v->d, v->cap * sizeof *v->d); }
  v->d[v->len++] = x;
}
int main(int argc, char** argv) {
  int64_t n = argc > 1 ? atoll(argv[1]) : 4000, s = 0;
  vec xs = {0};
  for (int64_t i = 1; i <= n; i++) with_item(&xs, i * 3);
  for (size_t i = 0; i < xs.len; i++) s += xs.d[i];
  free(xs.d);
  printf("%lld\n", (long long)s);
  return 0;
}
