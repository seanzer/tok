"""The net module: a blocking TCP client and HTTP GET. Every test talks to a server started here, on this machine."""
import http.server
import os
import socket
import socketserver
import threading
import unittest

from helpers import TokCase, tokc


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/missing":
            self.send_error(404)
            return
        if self.path == "/moved":
            self.send_response(302)
            self.send_header("Location", "/hello")
            self.end_headers()
            return
        body = b"x" * 200000 if self.path == "/big" else f"hello from {self.path} via {self.headers['Host']}\n".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class Upper(socketserver.StreamRequestHandler):
    """Reads until the client closes its sending side, answers with the text in upper case."""
    def handle(self):
        self.wfile.write(self.rfile.read().upper())


class Threaded(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class NetTests(TokCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.tcp = Threaded(("127.0.0.1", 0), Upper)
        for s in (cls.http, cls.tcp):
            threading.Thread(target=s.serve_forever, daemon=True).start()
        cls.hp, cls.tp = cls.http.server_address[1], cls.tcp.server_address[1]
        with socket.socket() as s:  # a port nothing listens on
            s.bind(("127.0.0.1", 0))
            cls.closed = s.getsockname()[1]

    @classmethod
    def tearDownClass(cls):
        for s in (cls.http, cls.tcp):
            s.shutdown()
            s.server_close()
        super().tearDownClass()

    def test_programs_without_net_have_no_socket_code(self):
        self.assertNotIn("socket", tokc("emit-c", self.write("plain.tok", "print(1)\n")).stdout)
        self.assertIn("#define TOK_NET 1", tokc("emit-c", self.write("n.tok", 'print(net.get("http://x/") or "")\n')).stdout)

    def test_http_get(self):
        src = (f'print(net.get("http://127.0.0.1:{self.hp}/a?b=1#frag")?)\n'
               f'print(net.get("http://localhost:{self.hp}")?)\n'
               f'print(net.get("http://127.0.0.1:{self.hp}/big")?.len())\n')
        self.assertEqual(self.run_src(src), f"hello from /a?b=1 via 127.0.0.1:{self.hp}\n\n"
                                            f"hello from / via localhost:{self.hp}\n\n200000\n")

    def test_http_failures_are_net_errors(self):
        src = (f'for u in ["http://127.0.0.1:{self.hp}/missing", "http://127.0.0.1:{self.hp}/moved", '
               f'"http://127.0.0.1:{self.closed}/", "https://127.0.0.1/", "http://", "http://127.0.0.1:99999/", '
               f'"http://127.0.0.1:{self.tp}/", "http://no such host/"]\n'
               '  print(net.get(u) or e -> "{e.kind == NetError} {e.msg}")\n')
        out = self.run_src(src).splitlines()
        self.assertEqual(len(out), 8)
        for line, part in zip(out, ["status 404", "status 302 (redirects are not followed)", "Connection refused",
                                    "only http:// URLs", "no host in the URL", "bad port", "not HTTP", "not a URL"]):
            self.assertTrue(line.startswith("true cannot "), line)
            self.assertIn(part, line)

    def test_an_unhandled_failure_ends_the_script(self):
        out, err = self.run_fail(f'print(net.get("http://127.0.0.1:{self.closed}/")?)\n', 1)
        self.assertEqual(out, "")
        self.assertIn("error: cannot connect to 127.0.0.1:", err)

    def test_tcp_request(self):
        src = (f'print(net.request("127.0.0.1", {self.tp}, "hello tcp")?)\n'
               f'print(net.request("127.0.0.1", {self.tp}, "ab".repeat(50000))?.len())\n'
               f'print(net.request("127.0.0.1", {self.closed}, "x") or e -> "{{e.kind}}")\n'
               'print(net.request("127.0.0.1", 0, "x") or e -> e.msg)\n'
               'print(net.request("", 80, "x") or e -> e.msg)\n')
        self.assertEqual(self.run_src(src), "HELLO TCP\n100000\nNetError\n"
                                            "cannot connect to '127.0.0.1': 0 is not a port (1 to 65535)\n"
                                            "cannot connect: '' is not a host name\n")

    def test_the_net_effect(self):
        fn = 'fn page(u: str) -> str{eff}\n  net.get(u)?\n\nfn main() ! io, net, err\n  print(page("http://x/")?.len())\n'
        e = self.check_err(fn.format(eff=" ! err"))
        self.assertEqual(e["code"], "E0201")
        self.assertIn("net", e["message"])
        self.assertEqual(self.codes(fn.format(eff=" ! net, err").replace("! io, net, err", "! io, err")), ["E0201"])
        self.assertEqual(tokc("check", self.write("ok.tok", fn.format(eff=" ! net, err"))).returncode, 0)
        self.assertEqual(self.codes("error NetError\nprint(1)\n"), ["E0102"])

    def test_requests_from_several_threads(self):
        src = (f'fn one(i: int) -> int ! net, err\n  net.get("http://127.0.0.1:{self.hp}/{{i}}")?.len()\n\n'
               'ts = par for i in 1..12 -> spawn one(i)\nvar n = 0\nfor t in ts\n  n += t.wait()?\nprint(n)\n'
               f'ls = par for i in 1..12 -> (net.request("127.0.0.1", {self.tp}, "m{{i}}") or "").len()\nprint(ls.sum())\n')
        want = sum(len(f"hello from /{i} via 127.0.0.1:{self.hp}\n") for i in range(1, 13))
        for t in ("1", "4"):
            r = tokc("run", self.write("par.tok", src), env=dict(self.env, TOK_THREADS=t))
            self.assertEqual((r.returncode, r.stdout), (0, f"{want}\n27\n"), r.stderr)


if __name__ == "__main__":
    unittest.main()
