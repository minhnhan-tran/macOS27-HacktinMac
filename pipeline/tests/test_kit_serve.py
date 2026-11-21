"""pipeline/kit-serve: the folder arrives as it is (files, modes, symbolic and hard links) through curl | tar."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

TOOL = Path(__file__).resolve().parents[1] / 'kit-serve'


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close()
    return p


class KitServe(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d) / 'M27', Path(d) / 'out'
            (src / 'kit' / 'sub').mkdir(parents=True); dst.mkdir()
            (src / 'install').write_text('#!/bin/sh\n'); os.chmod(src / 'install', 0o755)
            (src / 'kit' / 'a').write_bytes(b'x' * 100000)
            os.link(src / 'kit' / 'a', src / 'kit' / 'sub' / 'a-twin')
            os.symlink('python3.12', src / 'kit' / 'python3')
            port = free_port()
            p = subprocess.Popen([sys.executable, '-I', str(TOOL), str(src), '--port', str(port), '--bind', '127.0.0.1'],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            try:
                p.stdout.readline()
                for _ in range(50):
                    try:
                        socket.create_connection(('127.0.0.1', port), 0.2).close(); break
                    except OSError:
                        time.sleep(0.1)
                r = subprocess.run('curl -fsS http://127.0.0.1:%d/m27.tar | tar -x -C %s' % (port, dst), shell=True)
                self.assertEqual(r.returncode, 0)
                size = subprocess.run(['curl', '-fsS', 'http://127.0.0.1:%d/size' % port], capture_output=True, text=True)
                self.assertTrue(size.stdout.strip().isdigit())
                self.assertEqual(subprocess.run(['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}',
                                                 'http://127.0.0.1:%d/etc/passwd' % port],
                                                capture_output=True, text=True).stdout, '404')
            finally:
                p.terminate(); p.wait()
            self.assertEqual((dst / 'kit' / 'a').read_bytes(), b'x' * 100000)
            self.assertEqual(os.stat(dst / 'install').st_mode & 0o777, 0o755)
            self.assertEqual(os.readlink(dst / 'kit' / 'python3'), 'python3.12')
            self.assertEqual(os.stat(dst / 'kit' / 'a').st_ino, os.stat(dst / 'kit' / 'sub' / 'a-twin').st_ino)
            self.assertEqual(sorted(x.name for x in dst.iterdir()), ['install', 'kit'])


if __name__ == '__main__':
    unittest.main()
