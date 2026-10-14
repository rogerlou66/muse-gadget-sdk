"""Exercise codec rollback and concurrent power/setters from production code."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AudioLifecycle(unittest.TestCase):
    def test_power_failure_and_concurrent_settings(self):
        source = (ROOT / "components/muse/muse_audio.c").read_text()
        lifecycle = source[source.index("static void lock(void)"):
                           source.index("static float to_db(")]
        prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <math.h>
#include <pthread.h>
#include <stdlib.h>
#include <sched.h>
#include "muse_audio_dsp.h"
#define CONFIG_MUSE_BOARD_ATK_DNESP32S3_BOX0 1
#define MUSE_AUDIO_HW_RATE 48000
#define MUSE_AUDIO_RATE 16000
#define CHANNELS 2
#define HPF_HZ 80.0f
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NO_MEM -2
#define ESP_CODEC_DEV_OK 0
#define ESP_LOGE(...) ((void)0)
#define ESP_LOGI(...) ((void)0)
#define ESP_LOG_NONE 0
#define portMAX_DELAY 0
#define ESP_RETURN_ON_FALSE(v, err, ...) do { if (!(v)) return err; } while (0)
typedef int esp_err_t;
typedef int esp_log_level_t;
typedef struct { bool open; } codec_t;
typedef codec_t *esp_codec_dev_handle_t;
typedef struct { int sample_rate, channel, bits_per_sample; } esp_codec_dev_sample_info_t;
typedef pthread_mutex_t *SemaphoreHandle_t;
typedef void *ns_handle_t;
static codec_t speaker, microphone;
static esp_codec_dev_handle_t s_spk, s_mic;
static bool s_open, powered, amp, fail_lock, fail_mic, fail_spk, fail_ns, fail_init;
static SemaphoreHandle_t s_lock;
static pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
static muse_audio_fir_t s_capture_fir, s_play_fir;
static muse_audio_gain_t s_capture_gain, s_play_gain;
static int s_last_play_rate;
static size_t s_clean_at;
static ns_handle_t s_ns;
static float s_hpf_a, s_hpf_x1, s_hpf_y1;
static int writes, closes, volume, gain, stored_volume = 40, stored_gain = 36;
static SemaphoreHandle_t xSemaphoreCreateMutex(void) { return fail_lock ? NULL : &mutex; }
static void xSemaphoreTake(SemaphoreHandle_t m, int wait) { (void)wait; assert(!pthread_mutex_lock(m)); }
static void xSemaphoreGive(SemaphoreHandle_t m) { assert(!pthread_mutex_unlock(m)); }
static void supply(bool on) {
    if (!on) assert(!speaker.open && !microphone.open && !amp);
    powered = on;
}
static void amplifier(bool on) {
    if (on) assert(powered && speaker.open && microphone.open &&
                   volume == stored_volume && gain == stored_gain);
    amp = on;
}
static int init(esp_codec_dev_handle_t *spk, esp_codec_dev_handle_t *mic) {
    assert(powered); *spk = &speaker; *mic = &microphone; return fail_init ? ESP_FAIL : ESP_OK;
}
static int esp_codec_dev_open(codec_t *c, const esp_codec_dev_sample_info_t *fs) {
    assert(powered && fs->sample_rate == 48000 && fs->channel == 2 && fs->bits_per_sample == 16);
    if ((c == &speaker && fail_spk) || (c == &microphone && fail_mic)) return ESP_FAIL;
    c->open = true; sched_yield(); return ESP_OK;
}
static int esp_codec_dev_close(codec_t *c) {
    assert(powered && !amp); c->open = false; closes++; sched_yield(); return ESP_OK;
}
static void esp_codec_dev_set_out_vol(codec_t *c, int v) {
    assert(powered && c == &speaker && c->open); volume = v; writes++;
}
static void esp_codec_dev_set_in_gain(codec_t *c, float v) {
    assert(powered && c == &microphone && c->open); gain = v; writes++;
}
static int muse_settings_volume(void) { return stored_volume; }
static int muse_settings_mic_gain(void) { return stored_gain; }
static int esp_log_level_get(const char *tag) { (void)tag; return 0; }
static void esp_log_level_set(const char *tag, int level) { (void)tag; (void)level; }
static ns_handle_t ns_pro_create(int ms, int mode, int rate) {
    assert(ms == 10 && mode == 0 && rate == 16000); return fail_ns ? NULL : (void *)1;
}
static struct {
    int (*audio_init)(esp_codec_dev_handle_t *, esp_codec_dev_handle_t *);
    void (*codec_power)(bool);
    void (*audio_power)(bool);
    void (*set_mic_gain)(esp_codec_dev_handle_t, int);
} board = {init, supply, amplifier, NULL}, *muse_board = &board;
void muse_audio_power(bool);
void muse_audio_set_volume(int);
void muse_audio_set_mic_gain(int);
'''
        suffix = r'''
static void *power_worker(void *unused) {
    (void)unused;
    for (int i = 0; i < 4000; i++) { muse_audio_power(false); muse_audio_power(true); }
    return NULL;
}
static void *settings_worker(void *unused) {
    (void)unused;
    for (int i = 0; i < 4000; i++) {
        muse_audio_set_volume(stored_volume); muse_audio_set_mic_gain(stored_gain);
    }
    return NULL;
}
int main(void) {
    fail_lock = true;
    assert(muse_audio_init(40, 36) == ESP_ERR_NO_MEM && !powered);
    fail_lock = false; fail_init = true;
    assert(muse_audio_init(40, 36) == ESP_FAIL && !powered);
    fail_init = false; fail_mic = true;
    assert(muse_audio_init(40, 36) == ESP_FAIL && !powered && !speaker.open && closes == 1);
    fail_mic = false; fail_ns = true;
    assert(muse_audio_init(40, 36) == ESP_ERR_NO_MEM && !powered && !s_open);
    fail_ns = false;
    assert(muse_audio_init(40, 36) == ESP_OK && powered && s_open);
    muse_audio_power(false);
    int before = writes;
    muse_audio_set_volume(50); muse_audio_set_mic_gain(30);
    assert(writes == before && !powered && !amp);
    fail_spk = true; muse_audio_power(true);
    assert(!powered && !amp && !s_open);
    fail_spk = false; fail_mic = true; muse_audio_power(true);
    assert(!powered && !speaker.open && !amp && !s_open);
    fail_mic = false;
    s_capture_fir.phase = 9; s_clean_at = 0; s_last_play_rate = 1;
    muse_audio_power(true);
    assert(powered && amp && s_open && s_capture_fir.phase == 0 &&
           s_clean_at == 160 && s_last_play_rate == 0);
    pthread_t a, b;
    assert(!pthread_create(&a, NULL, power_worker, NULL));
    assert(!pthread_create(&b, NULL, settings_worker, NULL));
    assert(!pthread_join(a, NULL) && !pthread_join(b, NULL));
    muse_audio_power(false);
    /* BOX0 keeps its existing rail ownership: optional callbacks are absent. */
    board.codec_power = NULL; board.audio_power = NULL; powered = true;
    muse_audio_power(true); assert(s_open && powered && !amp);
    muse_audio_power(false); assert(!s_open && powered && !amp);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "audio.c").write_text(prefix + lifecycle + suffix)
            built = subprocess.run([
                "cc", "-std=gnu11", "-pthread", "-fsanitize=address,undefined",
                "-I" + str(ROOT / "components/muse"), str(p / "audio.c"),
                "-o", str(p / "audio"), "-lm"], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(p / "audio")], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
