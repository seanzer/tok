"""The network runtime. It is part of a generated program only when the program calls the `net` module; every
other program has no socket code in it.

What is here: a blocking TCP client over plain sockets, and an HTTP GET built on it.
  - net.request(host, port, data): connect, send `data`, close the sending side, read until the peer closes.
  - net.get(url): an HTTP/1.0 GET of an http:// URL. Gives the body. A status of 400 or more is an error.

What is not here: TLS (so no https), redirects, chunked bodies (HTTP/1.0 asks for none), request headers, servers,
connections kept open, or anything that does not block. Each send and receive gives up after 30 seconds.
"""

PRELUDE = r'''#define TOK_NET 1
#ifndef _POSIX_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#endif
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <netdb.h>
#include <unistd.h>
'''

RUNTIME = r'''
// ---- net: a blocking TCP client. Every failure is an error of kind NetError.
#define TOK_NET_TIMEOUT 30
// A call into the network parks this OS thread; in a program with threads the pool is told, as for a channel.
#ifdef TOK_MT
#define TOK_NET_BEGIN() tok_block_begin()
#define TOK_NET_END() tok_block_end()
#else
#define TOK_NET_BEGIN() ((void)0)
#define TOK_NET_END() ((void)0)
#endif
static int tok_net_connect(const char* host, int64_t port, const char** err) {
  if (port < 1 || port > 65535) { *err = tok_fmt("cannot connect to '%s': %lld is not a port (1 to 65535)", host, (long long)port); return -1; }
  char service[16];
  snprintf(service, sizeof service, "%d", (int)port);
  struct addrinfo hints, *list = NULL;
  memset(&hints, 0, sizeof hints);
  hints.ai_family = AF_UNSPEC;
  hints.ai_socktype = SOCK_STREAM;
  int rc = getaddrinfo(host, service, &hints, &list);
  if (rc != 0) { *err = tok_fmt("cannot connect to %s:%d: %s", host, (int)port, gai_strerror(rc)); return -1; }
  int fd = -1, last = 0;
  for (struct addrinfo* a = list; a; a = a->ai_next) {
    fd = socket(a->ai_family, a->ai_socktype, a->ai_protocol);
    if (fd < 0) { last = errno; continue; }
    struct timeval tv = { TOK_NET_TIMEOUT, 0 };
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
    if (connect(fd, a->ai_addr, a->ai_addrlen) == 0) break;
    last = errno;
    close(fd);
    fd = -1;
  }
  freeaddrinfo(list);
  if (fd < 0) *err = tok_fmt("cannot connect to %s:%d: %s", host, (int)port, strerror(last));
  return fd;
}
// sends all of data, then reads until the peer closes; NULL and *err on failure
static const char* tok_net_exchange(int fd, const char* data, int64_t n, const char* host, int64_t port, const char** err) {
  int64_t sent = 0;
  while (sent < n) {
    ssize_t k = send(fd, data + sent, (size_t)(n - sent), MSG_NOSIGNAL);
    if (k < 0 && errno == EINTR) continue;
    if (k <= 0) { *err = tok_fmt("cannot send to %s:%d: %s", host, (int)port, strerror(errno)); return NULL; }
    sent += k;
  }
  shutdown(fd, SHUT_WR);
  tok_sb b;
  tok_sb_heap(&b, 4096);
  for (;;) {
    if (b.cap - b.len < 2048) tok_sb_grow(&b, 4096);
    ssize_t k = recv(fd, b.p + b.len, (size_t)(b.cap - b.len), 0);
    if (k < 0 && errno == EINTR) continue;
    if (k < 0) {
      *err = tok_fmt("cannot read from %s:%d: %s", host, (int)port,
                     errno == EAGAIN || errno == EWOULDBLOCK ? "timed out" : strerror(errno));
      tok_srelease(tok_sb_str(&b));
      return NULL;
    }
    if (k == 0) break;
    b.len += k;
  }
  return tok_sb_str(&b);
}
TOK_UNUSED static tok_T_Estr tok_net_request(const char* host, int64_t port, const char* data) {
  tok_T_Estr r = { NULL, TOK_KIND_NET, NULL };
  if (strlen(host) != (size_t)tok_slen(host) || !*host) { r.err = tok_fmt("cannot connect: '%s' is not a host name", host); return r; }
  TOK_NET_BEGIN();
  int fd = tok_net_connect(host, port, &r.err);
  if (fd >= 0) {
    r.v = tok_net_exchange(fd, data, tok_slen(data), host, port, &r.err);
    close(fd);
  }
  TOK_NET_END();
  return r;
}
TOK_UNUSED static tok_T_Estr tok_net_get(const char* url) {
  tok_T_Estr r = { NULL, TOK_KIND_NET, NULL };
  if (strlen(url) != (size_t)tok_slen(url) || strpbrk(url, " \r\n")) { r.err = tok_fmt("cannot get '%s': not a URL", url); return r; }
  if (strncmp(url, "http://", 7) != 0) {
    r.err = tok_fmt("cannot get '%s': only http:// URLs are supported (no TLS)", url);
    return r;
  }
  const char* h = url + 7;
  size_t hl = strcspn(h, "/?#");
  const char* path = h + hl;
  size_t pl = strcspn(path, "#");
  char host[256];
  int64_t port = 80;
  const char* colon = memchr(h, ':', hl);
  size_t nl = colon ? (size_t)(colon - h) : hl;
  if (nl == 0 || nl >= sizeof host) { r.err = tok_fmt("cannot get '%s': no host in the URL", url); return r; }
  memcpy(host, h, nl);
  host[nl] = 0;
  if (colon) {
    port = 0;
    const char* p = colon + 1;
    if (p == h + hl) port = -1;
    for (; p < h + hl; p++) {
      if (*p < '0' || *p > '9' || port > 65535) { port = -1; break; }
      port = port * 10 + (*p - '0');
    }
    if (port < 1 || port > 65535) { r.err = tok_fmt("cannot get '%s': bad port in the URL", url); return r; }
  }
  tok_sb q;
  tok_sb_heap(&q, 256);
  tok_sb_addz(&q, "GET ");
  if (pl == 0 || path[0] != '/') tok_sb_addz(&q, "/");
  tok_sb_add(&q, path, (int64_t)pl);
  tok_sb_addz(&q, " HTTP/1.0\r\nHost: ");
  tok_sb_add(&q, h, (int64_t)hl);
  tok_sb_addz(&q, "\r\nUser-Agent: tok\r\nAccept: */*\r\nConnection: close\r\n\r\n");
  const char* req = tok_sb_str(&q);
  TOK_NET_BEGIN();
  int fd = tok_net_connect(host, port, &r.err);
  const char* resp = NULL;
  if (fd >= 0) {
    resp = tok_net_exchange(fd, req, tok_slen(req), host, port, &r.err);
    close(fd);
  }
  TOK_NET_END();
  tok_srelease(req);
  if (!resp) return r;
  int64_t n = tok_slen(resp), body = -1;
  for (int64_t i = 0; i + 3 < n; i++) if (memcmp(resp + i, "\r\n\r\n", 4) == 0) { body = i + 4; break; }
  int status = 0;
  if (body < 0 || n < 12 || memcmp(resp, "HTTP/1.", 7) != 0 || sscanf(resp + 8, " %3d", &status) != 1 || status < 100) {
    r.err = tok_fmt("cannot get '%s': the answer is not HTTP", url);
  } else if (status >= 400) {
    r.err = tok_fmt("cannot get '%s': status %d", url, status);
  } else if (status >= 300) {
    r.err = tok_fmt("cannot get '%s': status %d (redirects are not followed)", url, status);
  } else {
    r.v = tok_sfrom(resp + body, n - body);
  }
  tok_srelease(resp);
  return r;
}
'''
