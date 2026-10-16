"""Run the actual screenshot script against a slow UART and mocked modem flags."""
import base64
from pathlib import Path
import runpy
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class MuseSnap(unittest.TestCase):
    def test_large_slow_screen_and_no_dtr_drop_on_close(self):
        clock = [0.0]
        state = {'start': None, 'head': False, 'writes': [], 'setattrs': []}
        row = b'\x00\xf8' * 412
        payload = b''.join(base64.b64encode(row[i:i+144]) + b'\n'
                           for _ in range(412) for i in range(0, len(row), 144)) + b'SNAP END\n'
        remaining = [payload]

        class Serial:
            fd = 7

            def __init__(self, port, baud, timeout):
                self.port = port
                self.baud = baud
                self.timeout = timeout

            def write(self, value):
                state['writes'].append(value)
                if value == b'p':
                    state['start'] = clock[0]

            def read(self, count):
                clock[0] += .2
                start = state['start']
                if start is None:
                    return b''
                if not state['head'] and clock[0] >= start + 1:
                    state['head'] = True
                    return b'SNAP BEGIN 412 412 144\n'
                if clock[0] >= start + 25:
                    data, remaining[0] = remaining[0][:count], remaining[0][count:]
                    return data
                return b''

            def __setattr__(self, key, value):
                if key in ('dtr', 'rts'):
                    raise AssertionError('Unrequested modem-line toggle could reset hardware')
                object.__setattr__(self, key, value)

        def now():
            clock[0] += .01
            return clock[0]

        def setattrs(fd, action, attrs):
            self.assertEqual(fd, 7)
            self.assertEqual(attrs[2] & 0x4000, 0)
            state['setattrs'].append(attrs)

        modules = {
            'serial': types.SimpleNamespace(Serial=Serial),
            'time': types.SimpleNamespace(time=now, sleep=lambda n: clock.__setitem__(0, clock[0] + n)),
            'termios': types.SimpleNamespace(HUPCL=0x4000, TCSANOW=0,
                tcgetattr=lambda fd: [0, 0, 0x4000, 0, 0, 0, []], tcsetattr=setattrs),
        }
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'screen.png'
            with patch.dict(sys.modules, modules), patch.object(sys, 'argv',
                    ['snap.py', 'fake-uart', '', str(output)]):
                result = runpy.run_path(str(ROOT / 'tools/muse/snap.py'), run_name='__main__')
            self.assertEqual(len(result['raw']), 412 * 412 * 2)
            self.assertEqual(result['raw'], row * 412)
            self.assertGreater(clock[0], state['start'] + 25)
            self.assertEqual(struct.unpack('>II', output.read_bytes()[16:24]), (1236, 1236))
        self.assertEqual(state['writes'], [b'p'])
        self.assertEqual(len(state['setattrs']), 1)
