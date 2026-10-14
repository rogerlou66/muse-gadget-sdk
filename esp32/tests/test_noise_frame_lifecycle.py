"""Check production WebSocket framing and request errors across reconnects."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NoiseFrameLifecycle(unittest.TestCase):
    def run_cpp(self, code):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "frames.cpp").write_text(code)
            built = subprocess.run([
                "c++", "-std=c++17", "-fsanitize=address,undefined",
                "-I" + str(ROOT / "main"), str(p / "frames.cpp"),
                "-o", str(p / "frames")], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(p / "frames")], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_one_write_mask_and_length_boundaries(self):
        source = (ROOT / "main/noise_control.cpp").read_text()
        sender = source[source.index("static bool ws_send_binary("):
                        source.index("// Send a zero-length masked WebSocket ping")]
        self.run_cpp(r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <vector>
#include <sys/types.h>
#include "ws_mask.h"
struct esp_tls_t {};
static std::vector<uint8_t> sent;
static int writes;
static bool fail;
static uint32_t esp_random() {return 0x12345678;}
static ssize_t write_all(esp_tls_t *, const uint8_t *b, size_t n) {
    writes++; if (fail) return -1; sent.insert(sent.end(), b, b+n); return n;
}
''' + sender + r'''
static void verify(size_t n) {
    assert(sent[0] == 0x82 && (sent[1] & 0x80));
    size_t head = 2, length = sent[1] & 127;
    if (length == 126) { length = (sent[2]<<8)|sent[3]; head=4; }
    if (length == 127) {
        length=0; for (int i=2; i<10; i++) length=(length<<8)|sent[i]; head=10;
    }
    assert(length == n && sent.size() == head+4+n);
    for (size_t i=0; i<n; i++) assert((sent[head+4+i]^sent[head+(i&3)]) == (i&255));
}
int main() {
    for (size_t n : {size_t(0),size_t(1),size_t(125),size_t(126),size_t(65535),size_t(65536)}) {
        std::vector<uint8_t> storage(n+32);
        for (size_t i=0; i<n; i++) storage[32+i]=i;
        sent.clear(); writes=0;
        assert(ws_send_frame_inplace(nullptr, storage.data()+32, n));
        assert(writes == 1); verify(n);
    }
    std::vector<uint8_t> data(100);
    for (size_t i=0; i<data.size(); i++) data[i]=i;
    sent.clear(); writes=0;
    assert(ws_send_binary(nullptr,data.data(),data.size())); assert(writes==1); verify(data.size());
    fail=true;
    assert(!ws_send_frame_inplace(nullptr,data.data()+32,10));
    assert(!ws_send_binary(nullptr,data.data(),data.size()));
}
''')

    def test_oversize_status_is_cleared_after_session_teardown(self):
        source = (ROOT / "main/noise_control.cpp").read_text()
        teardown = source[source.index("static void req_fail("):
                          source.index("static bool req_open(")]
        self.run_cpp(r'''
#include <cassert>
#include <cstdint>
#include <cstddef>
#define NOISE_CTRL_REQ_TOO_LARGE (-2)
using callback = void (*)(void *,int,const uint8_t *,size_t,bool);
struct req_stream {int id; callback cb; void *ctx;};
struct req_op {};
static req_stream s_req_streams[3];
static bool s_req_too_large;
static bool req_take(req_op *) {return false;}
static void req_drop(req_op &) {}
static int calls, received;
static void result(void *,int status,const uint8_t *p,size_t n,bool end) {
    assert(!p && !n && end); calls++; received=status;
}
''' + teardown + r'''
int main() {
    s_req_streams[0]={1,result,nullptr}; s_req_streams[1]={2,result,nullptr};
    s_req_too_large=true; req_end_all();
    assert(calls==2 && received==-2 && !s_req_too_large && !s_req_streams[0].id && !s_req_streams[1].id);
    req_end_all(); assert(calls==2);
    s_req_streams[2]={3,result,nullptr}; req_end_all();
    assert(calls==3 && received==-1 && !s_req_streams[2].id);
}
''')
