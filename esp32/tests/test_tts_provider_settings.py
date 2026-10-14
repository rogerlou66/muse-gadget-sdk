"""Provider switches preserve ASR credentials and reject invalid region/rate."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class ProviderSettings(unittest.TestCase):
    def test_credentials_region_speed_and_provider_round_trip(self):
        source=(ROOT/'components/muse/muse_settings.c').read_text()
        setters=source[source.index('static esp_err_t set_speech_key('):source.index('/* Home Link owns the saved networks')]
        getters='\n'.join(l for l in source.splitlines() if l.startswith(('bool muse_settings_tts_configured','bool muse_settings_openai_configured')))
        code='''
#include <assert.h>
#include <string.h>
#include <stdlib.h>
#include <math.h>
#include "host_compat.h"
#include "muse_settings.h"
#define ESP_OK 0
#define ESP_ERR_INVALID_ARG 1
#define ESP_ERR_INVALID_SIZE 2
#define LOCKED(body) do {body;} while(0)
static struct {muse_tts_config_t tts;} s;
static int s_nvs;
static uint16_t saved_speed;
static int nvs_set_u16(int h,const char *k,uint16_t v) {(void)h;(void)k;saved_speed=v;return 0;}
static int nvs_commit(int h) {(void)h;return 0;}
static void save_str(const char *key,const char *value) {(void)key;(void)value;}
static void save_u8(const char *key,uint8_t value) {(void)key;(void)value;}
''' + getters + setters + '''
int main(void) {
    s.tts.speed_pct=125;strcpy(s.tts.model,"gpt-4o-mini-tts");strcpy(s.tts.voice,"shimmer");
    assert(muse_settings_set_tts_key("openai-test",false)==ESP_OK);
    assert(muse_settings_tts_configured() && muse_settings_openai_configured());
    assert(muse_settings_set_tts_option("provider","qwen")==ESP_OK);
    assert(!muse_settings_tts_configured() && muse_settings_openai_configured());
    assert(muse_settings_set_qwen_key("qwen-",false)==ESP_OK);
    assert(muse_settings_set_qwen_key("test",true)==ESP_OK);
    assert(!strcmp(s.tts.qwen_key,"qwen-test") && !strcmp(s.tts.key,"openai-test"));
    assert(muse_settings_set_qwen_key("bad\\r\\nHost: evil",false)!=ESP_OK);
    assert(!strcmp(s.tts.qwen_key,"qwen-test"));
    assert(muse_settings_tts_configured() && muse_settings_openai_configured());
    assert(muse_settings_set_tts_option("region","virginia")!=ESP_OK);
    assert(muse_settings_set_tts_option("region","singapore")==ESP_OK);
    assert(muse_settings_set_tts_option("voice","Cherry")==ESP_OK);
    assert(muse_settings_set_tts_option("model","qwen3-tts-flash-realtime")==ESP_OK);
    assert(!strcmp(s.tts.qwen_voice,"Cherry") && !strcmp(s.tts.voice,"shimmer"));
    assert(muse_settings_set_tts_option("speed","1.25")==ESP_OK && saved_speed==125);
    assert(muse_settings_set_tts_option("speed","0.25")!=ESP_OK);
    assert(muse_settings_set_tts_option("speed","2.01")!=ESP_OK);
    assert(muse_settings_set_tts_option("speed","NaN")!=ESP_OK);
    assert(muse_settings_set_tts_option("speed","1.25junk")!=ESP_OK);
    assert(muse_settings_set_tts_option("provider","openai")==ESP_OK);
    assert(!strcmp(s.tts.voice,"shimmer") && !strcmp(s.tts.model,"gpt-4o-mini-tts"));
    assert(muse_settings_set_tts_option("speed","0.25")==ESP_OK);
    assert(muse_settings_set_tts_option("provider","qwen")!=ESP_OK);
}
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'esp_err.h').write_text('typedef int esp_err_t;\n');(p/'settings.c').write_text(code)
            subprocess.run(['cc','-std=c11','-I'+str(p),'-I'+str(ROOT/'tests'),
                '-I'+str(ROOT/'components/muse'),str(p/'settings.c'),'-o',str(p/'settings')],check=True)
            subprocess.run([str(p/'settings')],check=True)
