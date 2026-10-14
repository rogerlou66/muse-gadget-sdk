"""Long BOX0 recordings reach ASR intact without the former 20-second cutoff."""
from pathlib import Path
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]

class RecordingDuration(unittest.TestCase):
    def test_completed_note_at_twenty_five_and_thirty_seconds(self):
        muse=ROOT/'components/muse'
        session=(muse/'muse_chat_session.cpp').read_text()
        voice=(muse/'muse_voice.c').read_text()
        text=(muse/'muse_chat_text.c').read_text()
        caps=voice[voice.index('#if CONFIG_MUSE_BOARD_ATK_DNESP32S3_BOX0\n#define MAX_SECS'):voice.index('#define MIN_HELD_FRAMES')]
        constants=session[session.index('#define MIC_RATE'):session.index('/* ---- Voice task')]
        record=session[session.index('static bool record_note('):session.index('/*\n * Posts `text`')]
        wav=text[text.index('static void put_le('):text.index('/* Last CAPTION_CHARS')]
        code=r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <cstdio>
#include <vector>
#include "muse_chat_priv.h"
#define CONFIG_MUSE_BOARD_ATK_DNESP32S3_BOX0 1
#define CONFIG_MUSE_HATCH 1
#define MUSE_AUDIO_RATE 16000
#define ESP_LOGI(...) ((void)0)
''' + caps + constants + r'''
enum {M_RELEASE,M_SENT,K_CHAT,P_LISTEN,P_WAIT_ASR,P_WAIT_REPLY};
static struct {
    int chat_id,asr_ticket,phase;
    size_t pcm_bytes,note_len,note_off,body_sent;
    bool end_requested,asr_bypass,chat_posted;
    uint8_t *note,*chunk;
    int64_t chat_us,last_event_us;
} s_turn;
static int s_in;
static std::vector<uint8_t> input;
static size_t available, consumed, queued;
static void mark(int) {}
static void turn_fail(const char *) {assert(false);}
static int64_t now_us() {return 1;}
static size_t xStreamBufferReceive(int,void *dst,size_t n,int) {
    n=n<available-consumed?n:available-consumed;
    memcpy(dst,input.data()+consumed,n);consumed+=n;return n;
}
static bool muse_asr_enabled() {return true;}
static uint32_t little(const uint8_t *p) {
    return p[0]|(uint32_t(p[1])<<8)|(uint32_t(p[2])<<16)|(uint32_t(p[3])<<24);
}
static int muse_asr_start(const uint8_t *p,size_t n) {
    assert(!memcmp(p,"RIFF",4)&&little(p+4)==n-8&&little(p+40)==n-44);
    assert(little(p+24)==16000&&!memcmp(p+44,input.data(),n-44));
    queued=n;return 7;
}
static int open_stream(int,const char *,const char *,const char *,const char *,const char *,bool) {assert(false);return 0;}
static bool send_body(int,const uint8_t *,size_t,bool) {assert(false);return false;}
''' + wav + record + r'''
static void begin() {
    s_turn={};consumed=available=queued=0;
    s_turn.note=static_cast<uint8_t *>(malloc(NOTE_MAX_BYTES+44));
    s_turn.note_len=44;s_turn.phase=P_LISTEN;
}
int main() {
    static_assert(MAX_FRAMES==480000 && NOTE_MAX_BYTES==MAX_FRAMES*2);
    input.resize(NOTE_MAX_BYTES+32000);
    for(size_t i=0;i<input.size();i++)input[i]=i%251;
    begin();available=25*32000;
    assert(record_note()&&!queued&&s_turn.pcm_bytes==available);
    s_turn.end_requested=true;assert(record_note());
    assert(queued==25*32000+44&&s_turn.phase==P_WAIT_ASR);free(s_turn.note);
    begin();available=input.size();assert(record_note());
    assert(queued==30*32000+44&&consumed==NOTE_MAX_BYTES&&s_turn.phase==P_WAIT_ASR);free(s_turn.note);
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'duration.cpp').write_text(code)
            r=subprocess.run(['c++','-std=c++17','-fsanitize=address,undefined','-I'+str(muse),str(p/'duration.cpp'),'-o',str(p/'duration')],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            r=subprocess.run([str(p/'duration')],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
