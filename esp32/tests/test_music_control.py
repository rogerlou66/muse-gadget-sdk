"""Exercise real music command/catalog/decoder lifecycle with fake RTOS queues."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]

class MusicControl(unittest.TestCase):
    def test_selection_pause_interrupt_and_missing_card_configuration(self):
        ffmpeg=shutil.which('ffmpeg')
        if not ffmpeg:raise unittest.SkipTest('ffmpeg required for MP3 fixtures')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);directory=p/'music';directory.mkdir()
            a=directory/'a.mp3'
            subprocess.run([ffmpeg,'-loglevel','error','-f','lavfi','-i','sine=frequency=440:sample_rate=44100:duration=1','-codec:a','libmp3lame',str(a)],check=True)
            shutil.copy(a,directory/'b.MP3');(directory/'folder.mp3').mkdir()
            shutil.copy(a,directory/'.hidden.mp3');(directory/'link.mp3').symlink_to(a)
            (p/'sdkconfig.h').write_text('#define CONFIG_MUSE_BOARD_ATK_DNESP32S3_BOX0 1\n')
            (p/'engine.inc').write_text('\n'.join(l for l in (ROOT/'components/muse/muse_music.c').read_text().splitlines()
                if not l.startswith(('#include "muse_', '#include "esp_', '#include "freertos/'))))
            prefix=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include "muse_music.h"
#include "muse_music_file.h"
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
#define MUSE_AUDIO_PLAY_CHUNK 960
#define MUSE_AUDIO_PLAY_RATE 48000
#define MUSE_BIG_CAPS 0
#define ESP_OK 0
#define pdTRUE 1
#define portMUX_TYPE pthread_mutex_t
#define portMUX_INITIALIZER_UNLOCKED PTHREAD_MUTEX_INITIALIZER
#define portENTER_CRITICAL(m) pthread_mutex_lock(m)
#define portEXIT_CRITICAL(m) pthread_mutex_unlock(m)
typedef int esp_err_t;
static bool fail_alloc,fail_mount;
static int mounts;
static void *heap_caps_calloc(size_t n,size_t sz,int caps) {(void)caps;return fail_alloc?NULL:calloc(n,sz);}
typedef struct {unsigned count,head,tail;size_t item;unsigned char bytes[1024];} queue_t;
typedef queue_t *QueueHandle_t;
static QueueHandle_t xQueueCreateWithCaps(int n,size_t item,int caps) {
 (void)caps;assert(n==8&&item*8<=1024);queue_t *q=calloc(1,sizeof(*q));q->item=item;return q;
}
static int xQueueSend(queue_t *q,const void *p,int wait) {
 (void)wait;if(q->count==8)return 0;memcpy(q->bytes+q->tail*q->item,p,q->item);q->tail=(q->tail+1)%8;q->count++;return 1;
}
static int xQueueReceive(queue_t *q,void *p,int wait) {
 (void)wait;if(!q->count)return 0;memcpy(p,q->bytes+q->head*q->item,q->item);q->head=(q->head+1)%8;q->count--;return 1;
}
static unsigned uxQueueMessagesWaiting(queue_t *q) {return q->count;}
static struct {int (*storage_mount)(const char *);} board,*muse_board=&board;
static int mount(const char *path) {assert(!strcmp(path,"/sdcard"));mounts++;return fail_mount?-1:0;}
'''
            suffix=r'''
static size_t poll(void) {const int16_t *pcm;size_t n=muse_music_poll(&pcm);assert(n<=960&&(!n||pcm));return n;}
static void command(muse_music_action_t action,unsigned track) {assert(muse_music_request(action,track));poll();}
int main(void) {
 fail_alloc=true;assert(!muse_music_init());fail_alloc=false;assert(muse_music_init());
 assert(!muse_music_request((muse_music_action_t)-1,0)&&!muse_music_request(MUSE_MUSIC_PLAY,64));
 command(MUSE_MUSIC_SCAN,0);assert(!s->status.mounted&&!mounts&&strstr(s->status.error,"NOT CONFIGURED"));
 board.storage_mount=mount;fail_mount=true;command(MUSE_MUSIC_SCAN,0);assert(!s->status.mounted&&mounts==1);
 fail_mount=false;command(MUSE_MUSIC_SCAN,0);assert(s->status.mounted&&s->status.count==2&&!strcmp(s->names[0],"a.mp3"));
 char name[128];muse_music_name(1,name,sizeof(name));assert(!strcmp(name,"b.MP3"));
 command(MUSE_MUSIC_PLAY,0);assert(s_file&&s->status.playing&&muse_music_take_reset());
 command(MUSE_MUSIC_TOGGLE,0);assert(s->status.paused&&!s->status.playing&&!poll());
 command(MUSE_MUSIC_TOGGLE,0);assert(!s->status.paused&&s->status.playing&&poll());
 /* A raw press silences without destroying a double-click menu's resume. */
 muse_music_suspend();assert(!poll()&&s_file&&s->status.paused);
 command(MUSE_MUSIC_TOGGLE,0);assert(s_file&&s->status.playing);
 command(MUSE_MUSIC_NEXT,0);assert(s->status.track==1);
 command(MUSE_MUSIC_PREVIOUS,0);assert(s->status.track==0);
 assert(muse_music_request(MUSE_MUSIC_NEXT,0));
 muse_music_interrupt();assert(muse_music_busy());assert(!poll());
 assert(!s_file&&!s->status.playing&&!muse_music_busy()&&muse_music_take_reset());
 /* A queued play must not resume music after the voice task takes ownership. */
 assert(muse_music_request(MUSE_MUSIC_PLAY,0));muse_music_stop_owned();assert(!poll()&&!s_file);
 command(MUSE_MUSIC_PLAY,0);
 int chunks=0;while(s->status.playing){poll();assert(++chunks<200);}
 assert(!s_file&&!s->status.error[0]);
 command(MUSE_MUSIC_PLAY,0);command(MUSE_MUSIC_TOGGLE,0);muse_music_interrupt();poll();
 assert(!s_file&&!s->status.paused);
 return 0;
}
'''
            (p/'main.c').write_text(prefix+'\n#include "engine.inc"\n'+suffix)
            src=ROOT/'components/muse'
            r=subprocess.run(['cc','-std=gnu11','-O1','-pthread','-fsanitize=address,undefined',
                '-DMUSE_MUSIC_DIRECTORY="'+str(directory)+'"','-I'+str(p),'-I'+str(src),'-I'+str(ROOT/'components/minimp3/include'),
                str(p/'main.c'),str(src/'muse_music_file.c'),'-lm','-o',str(p/'control')],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            r=subprocess.run([str(p/'control')],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
