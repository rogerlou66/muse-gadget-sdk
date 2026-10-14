"""Production multipart/WAV parsing and stale-transcript lifecycle checks."""
import ctypes
import io
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]

class ASR(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        out = Path(cls.tmp.name)
        src = ROOT/'components/muse'
        cj = ROOT/'managed_components/espressif__cjson/cJSON'
        lib = out/'asr.dylib'
        subprocess.run(['cc','-shared','-fPIC','-I'+str(cj),str(src/'muse_asr_payload.c'),
            str(cj/'cJSON.c'),'-o',str(lib)],check=True)
        cls.lib = ctypes.CDLL(str(lib))
        cls.lib.muse_asr_form.argtypes = [ctypes.c_char_p]*4+[ctypes.c_size_t]
        cls.lib.muse_asr_form.restype = ctypes.c_int
        cls.lib.muse_asr_parse.argtypes = [ctypes.c_char_p,ctypes.c_char_p,ctypes.c_size_t]
        cls.lib.muse_asr_parse.restype = ctypes.c_bool
        cls.lib.muse_asr_text_empty.argtypes = [ctypes.c_char_p]
        cls.lib.muse_asr_text_empty.restype = ctypes.c_bool
        cls.lib.muse_asr_valid_wav.argtypes = [ctypes.c_char_p,ctypes.c_size_t]
        cls.lib.muse_asr_valid_wav.restype = ctypes.c_bool
        source = (src/'muse_asr.c').read_text()
        lifecycle = source[source.index('void muse_asr_cancel('):source.index('int muse_asr_last_http(')]
        session = (src/'muse_chat_session.cpp').read_text()
        poll = session[session.index('static void poll_asr('):session.index('/* A typed turn:')]
        harness = out/'asr.cpp'
        harness.write_text('''
#include <atomic>
#include <cassert>
#include <cstring>
#include <cstdlib>
#include <cstdio>
#include "host_compat.h"
#include "muse_asr.h"
#define atomic_load(p) ((p)->load())
#define atomic_store(p,v) ((p)->store(v))
struct result_t {uint32_t ticket;bool ok,no_speech;char text[MUSE_ASR_TEXT_MAX];};
static std::atomic_uint s_ticket;
static int s_results;
static result_t *pending;
static bool current(uint32_t t) {return s_ticket.load()==t;}
#define pdTRUE 1
static int xQueueReceive(int, result_t **out,int) {
    if(!pending)return 0; *out=pending;pending=nullptr;return 1;
}
''' + lifecycle + '''
static void result(unsigned ticket,bool ok,const char *text) {
    pending=(result_t *)calloc(1,sizeof(*pending));
    pending->ticket=ticket;pending->ok=ok;strlcpy(pending->text,text,sizeof(pending->text));
}
#define CONFIG_MUSE_HATCH 1
#include "muse_chat.h"
#define EXT_RAM_BSS_ATTR
#define ESP_LOGW(...) ((void)0)
#define ESP_LOGI(...) ((void)0)
enum {P_WAIT_ASR,P_WAIT_REPLY,P_IDLE};
static struct {int phase;uint32_t asr_ticket;uint32_t gen;} s_turn;
static std::atomic_uint s_gen;
static int posted,failed,events,rejected;
static void emit(muse_hatch_ev_t type,const char *) {if(type==MUSE_HATCH_EV_INPUT_INVALID)rejected++;}
static char sent[MUSE_ASR_TEXT_MAX];
int muse_asr_last_http(void) {return 200;}
void muse_hatch_console(const char *,const char *,const char *,...) {events++;}
static void post_chat(const char *text) {posted++;strlcpy(sent,text,sizeof(sent));s_turn.phase=P_WAIT_REPLY;}
static void turn_fail(const char *) {failed++;s_turn.phase=P_IDLE;}
static void turn_finish() {muse_asr_cancel(s_turn.asr_ticket);s_turn.phase=P_IDLE;}
''' + poll + '''
int main() {
    char text[2048]={};
    s_ticket=2;result(1,true,"stale");
    assert(muse_asr_poll(2,text,sizeof(text))==0 && !text[0]);
    result(2,true,"fresh");
    assert(muse_asr_poll(1,text,sizeof(text))==-2 && pending);
    assert(muse_asr_poll(2,text,3)==-1); // never send a truncated transcript
    muse_asr_cancel(1);assert(s_ticket==2);
    s_turn={P_WAIT_ASR,2};poll_asr();assert(!posted&&!failed);
    result(2,true,"你好，Muse");poll_asr();
    assert(posted==1&&events==1&&s_turn.phase==P_WAIT_REPLY&&s_ticket==0);
    assert(!strcmp(sent,"你好，Muse"));poll_asr();assert(posted==1);
    s_ticket=3;s_turn={P_WAIT_ASR,3};result(3,false,"");poll_asr();
    assert(failed==1&&posted==1&&s_turn.phase==P_IDLE);
    s_ticket=4;s_turn={P_WAIT_ASR,4};muse_asr_cancel(4);poll_asr();
    assert(failed==2&&posted==1);
    s_ticket=5;s_turn={P_WAIT_ASR,5,10};s_gen=11;
    result(5,true,"cancelled before queued command runs");poll_asr();
    assert(posted==1&&s_turn.phase==P_IDLE&&s_ticket==0);
    free(pending);pending=nullptr;
    s_gen=12;s_ticket=6;s_turn={P_WAIT_ASR,6,12};
    result(6,false,"");pending->no_speech=true;poll_asr();
    assert(rejected==1&&posted==1&&failed==2&&s_turn.phase==P_IDLE);
}
''')
        cls.lifecycle = out/'lifecycle'
        subprocess.run(['c++','-std=c++17','-I'+str(src),'-I'+str(ROOT/'tests'),
            str(harness),'-o',str(cls.lifecycle)],check=True)

    def test_exact_multipart_and_chinese_context(self):
        out = ctypes.create_string_buffer(1024)
        n = self.lib.muse_asr_form(b'gpt-transcribe',b'zh','中英文设备操作'.encode(),out,1024)
        self.assertGreater(n,0)
        body = out.value.decode()
        self.assertIn('name="languages[]"\r\n\r\nzh\r\n',body)
        self.assertIn('中英文设备操作',body)
        self.assertTrue(body.endswith('Content-Type: audio/wav\r\n\r\n'))
        self.assertEqual(n,len(out.value))
        self.assertGreater(self.lib.muse_asr_form(b'gpt-4o-transcribe',b'zh',b'',out,1024),0)
        self.assertIn(b'name="language"',out.value)

    def test_reject_injection_or_truncated_form(self):
        out = ctypes.create_string_buffer(1024)
        for prompt in (b'bad\r\n--injection',b'MuseASRFormBoundary_v1'):
            self.assertEqual(self.lib.muse_asr_form(b'gpt-transcribe',b'zh',prompt,out,1024),-1)
        self.assertEqual(self.lib.muse_asr_form(b'gpt-transcribe',b'zh',b'',out,10),-1)

    def test_transcript_valid_empty_error_and_limit(self):
        out = ctypes.create_string_buffer(64)
        data = json.dumps({'text':'  今天下午三点。\n'},ensure_ascii=False).encode()
        self.assertTrue(self.lib.muse_asr_parse(data,out,64))
        self.assertEqual(out.value.decode(),'今天下午三点。')
        for data in (b'{',b'{"error":{}}',b'{"text":123}',b'{"text":"  "}',b'{"text":"'+b'x'*80+b'"}'):
            self.assertFalse(self.lib.muse_asr_parse(data,out,64))
            self.assertEqual(out.value,b'')

    def test_wav_exact_rate_length_and_format(self):
        stream = io.BytesIO()
        with wave.open(stream,'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000)
            wav.writeframes(b'\0\0'*8000)
        good = stream.getvalue()
        self.assertTrue(self.lib.muse_asr_valid_wav(good,len(good)))
        for offset,value in ((4,0xffffffff),(24,24000),(40,100)):
            bad = bytearray(good);struct.pack_into('<I',bad,offset,value)
            self.assertFalse(self.lib.muse_asr_valid_wav(bytes(bad),len(bad)))
        self.assertFalse(self.lib.muse_asr_valid_wav(good,len(good)-1))
        self.assertFalse(self.lib.muse_asr_valid_wav(b'RIFF',4))

    def test_empty_speech_is_distinct_from_bad_service_response(self):
        for data in (b'{"text":""}',b'{"text":" \\n\\t"}'):
            self.assertTrue(self.lib.muse_asr_text_empty(data))
        for data in (b'{',b'{"error":{}}',b'{"text":123}',b'{"text":"hello"}'):
            self.assertFalse(self.lib.muse_asr_text_empty(data))

    def test_old_cancelled_failed_or_partial_text_is_not_sent(self):
        subprocess.run([str(self.lifecycle)],check=True)
