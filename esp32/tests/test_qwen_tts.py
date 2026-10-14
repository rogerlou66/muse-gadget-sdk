"""Qwen wire parameters, event rejection and fragmented text reassembly."""
import ctypes
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class QwenTTS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.tmp.cleanup)
        out=Path(cls.tmp.name);src=ROOT/'components/muse';cj=ROOT/'managed_components/espressif__cjson/cJSON'
        (out/'esp_err.h').write_text('typedef int esp_err_t;\n')
        lib=out/'qwen.dylib'
        subprocess.run(['cc','-shared','-fPIC','-I'+str(cj),'-I'+str(out),
            str(src/'muse_qwen_payload.c'),str(src/'muse_tts_payload.c'),str(cj/'cJSON.c'),'-o',str(lib)],check=True)
        cls.lib=ctypes.CDLL(str(lib))
        cls.lib.cJSON_free.argtypes=[ctypes.c_void_p]
        cls.lib.muse_qwen_session.argtypes=[ctypes.c_char_p,ctypes.c_double,ctypes.c_char_p]
        cls.lib.muse_qwen_session.restype=ctypes.c_void_p
        cls.lib.muse_qwen_text.argtypes=[ctypes.c_char_p,ctypes.c_char_p]
        cls.lib.muse_qwen_text.restype=ctypes.c_void_p
        cls.lib.muse_qwen_event.argtypes=[ctypes.c_char_p,ctypes.POINTER(ctypes.c_void_p)]
        cls.lib.muse_qwen_event.restype=ctypes.c_int
        (out/'frame.c').write_text('''
#include <assert.h>
#include "muse_qwen_frame.h"
int main(void) {
    muse_qwen_frame_t s={0};char out[64];
    assert(muse_qwen_frame_add(&s,1,false,4,"ab",2,out,sizeof(out))==0);
    assert(muse_qwen_frame_add(&s,1,false,4,"cd",2,out,sizeof(out))==0);
    assert(muse_qwen_frame_add(&s,0,true,2,"ef",2,out,sizeof(out))==1);
    assert(!strcmp(out,"abcdef"));
    assert(muse_qwen_frame_add(&s,1,true,2,"{}",2,out,sizeof(out))==1);
    assert(!strcmp(out,"{}"));
    assert(muse_qwen_frame_add(&s,0,true,1,"a",1,out,sizeof(out))==-1);
    s=(muse_qwen_frame_t){0};
    assert(muse_qwen_frame_add(&s,1,true,100,"xxxx",4,out,4)==-1);
    s=(muse_qwen_frame_t){0};
    assert(muse_qwen_frame_add(&s,1,false,1,"a",1,out,sizeof(out))==0);
    assert(muse_qwen_frame_add(&s,1,true,1,"b",1,out,sizeof(out))==-1);
    s=(muse_qwen_frame_t){0};
    assert(muse_qwen_frame_add(&s,2,true,1,"b",1,out,sizeof(out))==-1);
}
''')
        cls.frame=out/'frame'
        subprocess.run(['cc','-std=c11','-Wall','-Werror','-I'+str(src),str(out/'frame.c'),'-o',str(cls.frame)],check=True)

    def decode(self,p):
        if not p:return None
        try:return json.loads(ctypes.string_at(p))
        finally:self.lib.cJSON_free(p)

    def test_cherry_chinese_mp3_and_numeric_speed(self):
        body=self.decode(self.lib.muse_qwen_session(b'Cherry',1.25,b'123'))
        self.assertEqual(body,{'event_id':'123','type':'session.update','session':{
            'voice':'Cherry','mode':'commit','language_type':'Chinese',
            'response_format':'mp3','sample_rate':24000,'speech_rate':1.25}})
        for speed in (.49,2.01,float('inf'),float('nan')):
            self.assertIsNone(self.decode(self.lib.muse_qwen_session(b'Cherry',speed,b'123')))

    def test_text_escaping_and_complete_utf8(self):
        text='你好，"世界"\n第二行'
        body=self.decode(self.lib.muse_qwen_text(text.encode(),b'456'))
        self.assertEqual(body['text'],text)
        body=self.decode(self.lib.muse_qwen_text(b'hello\xe4\xbd',b'456'))
        self.assertEqual(body['text'],'hello')

    def event(self,obj):
        out=ctypes.c_void_p()
        result=self.lib.muse_qwen_event(json.dumps(obj).encode(),ctypes.byref(out))
        data=ctypes.string_at(out).decode() if out.value else None
        if out.value:self.lib.cJSON_free(out)
        return result,data

    def test_ack_requires_requested_audio_encoding(self):
        self.assertEqual(self.event({'type':'session.updated','session':{'response_format':'mp3'}})[0],2)
        self.assertEqual(self.event({'type':'session.updated','session':{'response_format':'pcm'}})[0],6)
        self.assertEqual(self.event({'type':'session.updated'})[0],6)

    def test_audio_and_terminal_events(self):
        self.assertEqual(self.event({'type':'response.audio.delta','delta':'AQID'}),(3,'AQID'))
        self.assertEqual(self.event({'type':'response.audio.delta','delta':''}),(0,None))
        self.assertEqual(self.event({'type':'response.audio.done'}),(4,None))
        self.assertEqual(self.event({'type':'session.finished'}),(5,None))
        self.assertEqual(self.event({'type':'error','error':{'code':'invalid_api_key'}}),(6,None))
        self.assertEqual(self.event({}),(6,None))

    def test_fragmented_reads_and_invalid_frame_order(self):
        subprocess.run([str(self.frame)],check=True)
