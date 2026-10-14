"""Invalid input is discarded; actual delivery failures still retain the note."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class EmptyNote(unittest.TestCase):
    def test_original_and_saved_notes_do_not_retry_empty_speech(self):
        src=(ROOT/'components/muse/muse_voice.c').read_text()
        wait=src[src.index('static bool wait_delivered('):src.index('/*\n * Sends the oldest saved note')]
        held=src[src.index('static bool send_held('):src.index('/*\n * Whether to send a saved note now')]
        finish=src[src.index('static bool finish_note('):src.index('/*\n * Whether a press can record')]
        code='''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include "muse_chat.h"
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
#define HOLD_NOTES 1
#define ACK_WAIT_US 30000000
#define MUSE_AUDIO_RATE 16000
#define HELD_TRIES 8
#define RETRY_MIN_US 15000000
#define pdMS_TO_TICKS(x) (x)
typedef enum {FED,FEED_PRESSED,FEED_FAILED} feed_t;
typedef struct {int16_t *pcm;size_t frames;int64_t at_us;int tries;} held_note_t;
static held_note_t s_held[1];
static int16_t storage[1],*s_rec;
static size_t s_sent,s_rec_n;
static bool s_live,s_tried;
static int dropped,retained,held_dropped,backoffs;
static int64_t s_next_send_us,s_send_backoff_us,clock_us;
static muse_hatch_ev_t next_event;
int64_t esp_timer_get_time(void) {clock_us+=1000;return clock_us;}
void vTaskDelay(int n) {clock_us+=(int64_t)n*1000;}
muse_hatch_ev_t muse_hatch_turn_event(char *text,size_t cap) {
    (void)text;(void)cap;muse_hatch_ev_t e=next_event;next_event=MUSE_HATCH_EV_NONE;return e;
}
void muse_hatch_turn_begin(void) {}
void muse_hatch_turn_end(void) {}
void muse_hatch_turn_cancel(void) {}
static void muse_audio_chirp(int n) {(void)n;}
static void muse_state_set_mode(int n) {(void)n;}
#define MUSE_MODE_THINKING 1
static void muse_state_set_caption(const char *fmt,...) {(void)fmt;}
static void go_idle(const char *s) {(void)s;}
static void drop_rec(void) {dropped++;s_rec=NULL;}
static void hold_rec(bool tried) {(void)tried;retained++;}
static void drop_oldest(void) {held_dropped++;}
static void back_off(void) {backoffs++;}
static feed_t feed_rest(const int16_t *p,size_t n,size_t *sent,bool y) {
    (void)p;(void)y;*sent=n;return FED;
}
static bool hatch_reply(bool *delivered,bool *discard) {
    *discard=next_event==MUSE_HATCH_EV_INPUT_INVALID;
    *delivered=next_event==MUSE_HATCH_EV_SENT;
    return false;
}
''' + wait + held + finish + '''
int main(void) {
    s_rec=storage;s_rec_n=s_sent=1;s_live=true;
    next_event=MUSE_HATCH_EV_INPUT_INVALID;finish_note();
    assert(dropped==1&&retained==0);
    s_rec=storage;next_event=MUSE_HATCH_EV_ERROR;finish_note();
    assert(dropped==1&&retained==1);
    s_held[0].pcm=storage;s_held[0].frames=1;
    next_event=MUSE_HATCH_EV_INPUT_INVALID;send_held(true);
    assert(held_dropped==1&&backoffs==0&&s_held[0].tries==0);
    next_event=MUSE_HATCH_EV_ERROR;send_held(true);
    assert(held_dropped==1&&backoffs==1&&s_held[0].tries==1);
    next_event=MUSE_HATCH_EV_SENT;send_held(true);assert(held_dropped==2);
}
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'note.c').write_text(code)
            subprocess.run(['cc','-std=c11','-I'+str(ROOT/'components/muse'),
                str(p/'note.c'),'-o',str(p/'note')],check=True)
            subprocess.run([str(p/'note')],check=True)
