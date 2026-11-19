"""最小 Redis 替身：仅支持本地端到端验证所需的 PING/RPUSH/BLPOP/LPUSH。"""
import socket
import socketserver
import threading
import time

lists = {}
cond = threading.Condition()


def _reply(text):
    return ("+" + text + "\r\n").encode()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        while True:
            line = self.rfile.readline()
            if not line:
                return
            if not line.startswith(b"*"):
                return
            n = int(line[1:])
            args = []
            for _ in range(n):
                self.rfile.readline()  # $len
                args.append(self.rfile.readline().strip())
            cmd = args[0].upper()
            if cmd == b"PING":
                self.wfile.write(_reply("PONG"))
            elif cmd in (b"RPUSH", b"LPUSH"):
                key = args[1].decode()
                vals = [a.decode() for a in args[2:]]
                with cond:
                    lists.setdefault(key, [])
                    if cmd == b"RPUSH":
                        lists[key].extend(vals)
                    else:
                        lists[key] = vals + lists[key]
                    cond.notify_all()
                self.wfile.write((":%d\r\n" % len(lists[key])).encode())
            elif cmd == b"BLPOP":
                key = args[1].decode()
                timeout = float(args[2])
                deadline = time.time() + timeout if timeout > 0 else None
                with cond:
                    while not lists.get(key):
                        remaining = deadline - time.time() if deadline else 1
                        if remaining <= 0:
                            self.wfile.write(b"*-1\r\n")
                            break
                        cond.wait(min(remaining, 1))
                    else:
                        val = lists[key].pop(0)
                        self.wfile.write(("*2\r\n$%d\r\n%s\r\n$%d\r\n%s\r\n"
                                          % (len(key), key, len(val), val)).encode())
            else:
                self.wfile.write(_reply("OK"))


class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    Server(("127.0.0.1", 6399), Handler).serve_forever()
