"""Exercise production JSON and stream ownership across cancellation."""
import ctypes
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class TTS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        out = Path(cls.tmp.name)
        cjson = ROOT / 'managed_components/espressif__cjson/cJSON'
        lib = out / 'payload.dylib'
        subprocess.run(['cc', '-shared', '-fPIC', '-I'+str(cjson),
            str(ROOT/'components/muse/muse_tts_payload.c'), str(cjson/'cJSON.c'),
            '-o', str(lib)], check=True)
        cls.lib = ctypes.CDLL(str(lib))
        cls.lib.muse_tts_payload.argtypes = [ctypes.c_char_p]*3+[ctypes.c_double]
        cls.lib.muse_tts_payload.restype = ctypes.c_void_p
        cls.lib.cJSON_free.argtypes = [ctypes.c_void_p]
        source = (ROOT/'components/muse/muse_tts.c').read_text()
        read = source[source.index('void muse_tts_cancel('):source.index('int muse_tts_last_http(')]
        harness = out/'ownership.c'
        harness.write_text('''
#include <assert.h>
#include <stdatomic.h>
#include <string.h>
#include "muse_tts.h"
static atomic_uint s_ticket, s_owner;
static atomic_int s_state;
static atomic_bool s_prefilled;
#define AUDIO_PREFILL (48*1024)
#define ESP_LOGI(...) ((void)0)
static int s_audio, remaining;
static bool current(uint32_t t) { return atomic_load(&s_ticket)==t; }
static size_t xStreamBufferReceive(int b, void *out, size_t cap, int ticks) {
    size_t n=remaining<(int)cap?remaining:cap;
    memset(out,42,n); remaining-=n; return n;
}
static size_t xStreamBufferBytesAvailable(int b) { return remaining; }
''' + read + '''
int main(void) {
    char audio[8]; int state;
    s_ticket=2; s_owner=1; remaining=6; s_state=MUSE_TTS_DONE;
    assert(muse_tts_read(1,audio,4,&state)==0 && state==MUSE_TTS_CANCELLED);
    assert(muse_tts_read(2,audio,4,&state)==0 && state==MUSE_TTS_ACTIVE);
    assert(remaining==6);
    s_owner=2;
    s_state=MUSE_TTS_ACTIVE;
    assert(muse_tts_read(2,audio,4,&state)==0 && remaining==6);
    remaining=AUDIO_PREFILL;
    assert(muse_tts_read(2,audio,4,&state)==4 && state==MUSE_TTS_ACTIVE);
    remaining=6;s_state=MUSE_TTS_DONE;
    assert(muse_tts_read(2,audio,4,&state)==4 && state==MUSE_TTS_ACTIVE);
    assert(muse_tts_read(2,audio,4,&state)==2 && state==MUSE_TTS_DONE);
    muse_tts_cancel(1); assert(s_ticket==2);
    muse_tts_cancel(2); assert(s_ticket==0 && s_owner==0);
    assert(muse_tts_read(2,audio,4,&state)==0 && state==MUSE_TTS_CANCELLED);
    s_ticket=3;s_owner=3;s_prefilled=false;remaining=2;s_state=MUSE_TTS_DONE;
    assert(muse_tts_read(3,audio,4,&state)==2 && state==MUSE_TTS_DONE);
}
''')
        cls.ownership = out/'ownership'
        subprocess.run(['cc', '-std=c11', '-I'+str(ROOT/'components/muse'),
            str(harness), '-o', str(cls.ownership)], check=True)

    def payload(self, text, speed=1.0):
        ptr = self.lib.muse_tts_payload(text, b'gpt-4o-mini-tts', b'marin', speed)
        if not ptr:
            return None
        try:
            return json.loads(ctypes.string_at(ptr).decode('utf-8'))
        finally:
            self.lib.cJSON_free(ptr)

    def test_chinese_and_json_escaping(self):
        text = '你好，"世界"。\n第二行\\结束'
        self.assertEqual(self.payload(text.encode()), dict(model='gpt-4o-mini-tts',
            voice='marin', input=text, response_format='mp3', speed=1.0))

    def test_utf8_truncated_at_reply_limit(self):
        self.assertEqual(self.payload(b'hello'+b'\xe4\xbd')['input'], 'hello')
        self.assertEqual(self.payload('你好'.encode()+b'\xf0\x9f')['input'], '你好')

    def test_invalid_unicode_is_never_sent(self):
        for bad in (b'\xc0\x80', b'\xed\xa0\x80', b'\xf4\x90\x80\x80', b'\x80'):
            self.assertIsNone(self.payload(bad))
            self.assertEqual(self.payload(b'ok'+bad)['input'], 'ok')
        self.assertIsNone(self.payload(b''))

    def test_cancelled_audio_cannot_reach_next_reply(self):
        subprocess.run([str(self.ownership)], check=True)

    def test_speed_is_numeric_and_invalid_values_never_reach_service(self):
        for speed in (.25,1.25,4):
            self.assertEqual(self.payload(b'hello',speed)['speed'],speed)
        for speed in (.24,4.01,float('nan'),float('inf')):
            self.assertIsNone(self.payload(b'hello',speed))
