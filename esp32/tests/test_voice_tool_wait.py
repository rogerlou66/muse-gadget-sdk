"""Long tool work must not hit the ordinary one-minute voice-reply timeout."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class VoiceToolWait(unittest.TestCase):
    def test_busy_progress_completion_and_hard_cap(self):
        source=(ROOT/'components/muse/muse_chat_session.cpp').read_text()
        constants=source[source.index('#define MIC_RATE'):source.index('/* ---- Voice task')]
        types=source[source.index('enum phase_t'):source.index('/* 10 KB')]
        check=source[source.index('static void check_turn('):source.index('/* ---- Inbound dispatch')]
        code='''
#include <cassert>
#include <cstdint>
#include <cstring>
#include "minimp3.h"
#include "muse_chat_priv.h"
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
''' + constants + types + '''
static turn_t s_turn;
static int64_t clock_us;
static int failed,finished;
static bool complete;
static int64_t now_us() {return clock_us;}
static void on_dictation_end(bool) {}
static void turn_fail(const char *) {failed++;}
static void turn_done(bool ok) {finished++;complete=ok;}
static void log_marks() {}
''' + check + '''
static void begin() {s_turn={};s_turn.phase=P_WAIT_REPLY;failed=finished=0;complete=false;}
int main() {
    begin();clock_us=61*1000000LL;check_turn();assert(failed==1);
    begin();s_turn.agent_busy=s_turn.agent_work_seen=true;
    clock_us=120*1000000LL;check_turn();assert(!failed&&!finished);
    clock_us=240*1000000LL;check_turn();assert(!failed&&!finished);
    clock_us=301*1000000LL;check_turn();assert(failed==1&&!finished);
    begin();s_turn.image_requested=true;clock_us=240*1000000LL;
    check_turn();assert(!failed&&!finished);
    clock_us=301*1000000LL;check_turn();assert(failed==1);
    assert(muse_chat_image_wait("画一只猫并显示在屏幕上"));
    assert(muse_chat_image_wait("Please DRAW a cat"));
    assert(!muse_chat_image_wait("请告诉我现在几点了"));
    begin();s_turn.agent_work_seen=true;s_turn.last_event_us=119*1000000LL;
    clock_us=120*1000000LL;check_turn();assert(!failed&&!finished);
    clock_us=180*1000000LL;check_turn();assert(failed==1);
    begin();s_turn.agent_busy=s_turn.agent_work_seen=true;s_turn.nmsgs=1;
    s_turn.msgs[0].done=true;s_turn.msgs[0].tts=TTS_FINISHED;
    clock_us=120*1000000LL;check_turn();assert(!failed&&!finished);
    s_turn.agent_busy=false;check_turn();assert(finished==1&&complete);
    begin();s_turn.text=true;s_turn.agent_busy=true;
    clock_us=240*1000000LL;check_turn();assert(!failed&&!finished);
}
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'wait.cpp').write_text(code)
            subprocess.run(['c++','-std=c++17','-I'+str(ROOT/'components/muse'),
                '-I'+str(ROOT/'components/minimp3/include'),str(p/'wait.cpp'),'-o',str(p/'wait')],check=True)
            subprocess.run([str(p/'wait')],check=True)
