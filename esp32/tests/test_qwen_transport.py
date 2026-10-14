"""Production Qwen transport exercised with scripted TLS/WebSocket events."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
FAKE=r'''
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_LOG_WARN 2
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
#define MALLOC_CAP_SPIRAM 1
#define MALLOC_CAP_8BIT 2
typedef void *esp_transport_handle_t;
typedef enum {WS_TRANSPORT_OPCODES_CONT=0,WS_TRANSPORT_OPCODES_TEXT=1,
    WS_TRANSPORT_OPCODES_CLOSE=8,WS_TRANSPORT_OPCODES_FIN=128} ws_transport_opcodes_t;
typedef struct {const char *ws_path;const char *auth;bool propagate_control_frames;} esp_transport_ws_config_t;
void esp_fill_random(void *,size_t);
void *heap_caps_malloc(size_t,int);
int64_t esp_timer_get_time(void);
void esp_log_level_set(const char *,int);
esp_err_t esp_crt_bundle_attach(void *);
esp_transport_handle_t esp_transport_ssl_init(void);
esp_transport_handle_t esp_transport_ws_init(esp_transport_handle_t);
void esp_transport_ssl_crt_bundle_attach(esp_transport_handle_t,esp_err_t (*)(void *));
void esp_transport_ws_set_path(esp_transport_handle_t,const char *);
esp_err_t esp_transport_ws_set_config(esp_transport_handle_t,const esp_transport_ws_config_t *);
int esp_transport_connect(esp_transport_handle_t,const char *,int,int);
int esp_transport_ws_get_upgrade_request_status(esp_transport_handle_t);
int esp_transport_ws_send_raw(esp_transport_handle_t,ws_transport_opcodes_t,const char *,int,int);
int esp_transport_read(esp_transport_handle_t,char *,int,int);
int esp_transport_ws_get_read_opcode(esp_transport_handle_t);
bool esp_transport_ws_get_fin_flag(esp_transport_handle_t);
int esp_transport_ws_get_read_payload_len(esp_transport_handle_t);
void esp_transport_close(esp_transport_handle_t);
void esp_transport_destroy(esp_transport_handle_t);
int mbedtls_base64_decode(unsigned char *,size_t,size_t *,const unsigned char *,size_t);
'''
HARNESS=r'''
#include "fake.h"
#include "muse_qwen_tts.h"
#include "cJSON.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>
static const char *packets[8];
static unsigned packet,offset,created,destroyed,sends,nonce,connects;
static int handshake,status_seen;
static bool attached,alive,cancel_audio;
static unsigned char collected[16];static size_t received;
static char ids[4][37];
void esp_fill_random(void *p,size_t n) {unsigned char *b=p;for(size_t i=0;i<n;i++)b[i]=(unsigned char)(i+nonce);nonce++;}
void *heap_caps_malloc(size_t n,int caps) {(void)caps;return malloc(n);}
int64_t esp_timer_get_time(void) {static int64_t t;t+=1000;return t;}
void esp_log_level_set(const char *s,int n) {(void)s;(void)n;}
esp_err_t esp_crt_bundle_attach(void *p) {(void)p;return ESP_OK;}
esp_transport_handle_t esp_transport_ssl_init(void) {created++;return malloc(1);}
esp_transport_handle_t esp_transport_ws_init(esp_transport_handle_t p) {assert(p);created++;return malloc(1);}
void esp_transport_ssl_crt_bundle_attach(esp_transport_handle_t t,esp_err_t (*f)(void *)) {assert(t&&f==esp_crt_bundle_attach);attached=true;}
void esp_transport_ws_set_path(esp_transport_handle_t t,const char *p) {assert(t&&strstr(p,"model=qwen3-tts-flash-realtime"));}
esp_err_t esp_transport_ws_set_config(esp_transport_handle_t t,const esp_transport_ws_config_t *c) {
    assert(t&&!strcmp(c->auth,"Bearer qwen-test")&&!c->propagate_control_frames);return 0;
}
int esp_transport_connect(esp_transport_handle_t t,const char *host,int port,int timeout) {
    assert(t&&attached&&port==443&&timeout>0&&!strcmp(host,"dashscope-intl.aliyuncs.com"));connects++;
    return handshake==101?0:-1;
}
int esp_transport_ws_get_upgrade_request_status(esp_transport_handle_t t) {(void)t;return handshake;}
int esp_transport_ws_send_raw(esp_transport_handle_t t,ws_transport_opcodes_t opcode,const char *data,int n,int timeout) {
    assert(t&&opcode==(1|128)&&timeout>0&&sends<4);
    cJSON *j=cJSON_ParseWithLength(data,n);assert(j);
    const char *type=cJSON_GetStringValue(cJSON_GetObjectItem(j,"type"));
    const char *id=cJSON_GetStringValue(cJSON_GetObjectItem(j,"event_id"));assert(id&&strlen(id)==36);
    for(unsigned i=0;i<sends;i++)assert(strcmp(ids[i],id));strcpy(ids[sends],id);
    const char *expected[]={"session.update","input_text_buffer.append","input_text_buffer.commit","session.finish"};
    assert(!strcmp(type,expected[sends]));
    if(sends==0) {
        cJSON *s=cJSON_GetObjectItem(j,"session");
        assert(!strcmp(cJSON_GetStringValue(cJSON_GetObjectItem(s,"voice")),"Cherry"));
        assert(!strcmp(cJSON_GetStringValue(cJSON_GetObjectItem(s,"response_format")),"mp3"));
        assert(cJSON_GetObjectItem(s,"speech_rate")->valuedouble==1.25);
    }
    if(sends==1)assert(!strcmp(cJSON_GetStringValue(cJSON_GetObjectItem(j,"text")),"你好"));
    sends++;cJSON_Delete(j);return n;
}
int esp_transport_read(esp_transport_handle_t t,char *out,int cap,int timeout) {
    (void)t;(void)timeout;
    if(!packets[packet])return -1;
    size_t total=strlen(packets[packet]),n=total-offset;
    if(n>7)n=7;if(n>(size_t)cap)n=cap;
    memcpy(out,packets[packet]+offset,n);offset+=n;return n;
}
int esp_transport_ws_get_read_opcode(esp_transport_handle_t t) {(void)t;return 1;}
bool esp_transport_ws_get_fin_flag(esp_transport_handle_t t) {(void)t;return true;}
int esp_transport_ws_get_read_payload_len(esp_transport_handle_t t) {
    (void)t;int n=strlen(packets[packet]);if(offset==(unsigned)n){offset=0;packet++;}return n;
}
void esp_transport_close(esp_transport_handle_t t) {assert(t);}
void esp_transport_destroy(esp_transport_handle_t t) {destroyed++;free(t);}
int mbedtls_base64_decode(unsigned char *out,size_t cap,size_t *len,const unsigned char *data,size_t n) {
    assert(cap>=3);if(n!=4)return -1;
    if(!memcmp(data,"AQID",4)){out[0]=1;out[1]=2;out[2]=3;}
    else if(!memcmp(data,"BAUG",4)){out[0]=4;out[1]=5;out[2]=6;}else return -1;
    *len=3;return 0;
}
static bool active(void *ctx) {(void)ctx;return alive;}
static bool audio(void *ctx,const void *p,size_t n) {
    (void)ctx;assert(received+n<=sizeof(collected));memcpy(collected+received,p,n);received+=n;
    if(cancel_audio)alive=false;return true;
}
static void status(void *ctx,int s) {(void)ctx;status_seen=s;}
static void reset(void) {
    packet=offset=created=destroyed=sends=connects=0;attached=false;received=0;alive=true;cancel_audio=false;status_seen=0;
    memset(packets,0,sizeof(packets));
    packets[0]="{\"type\":\"session.created\"}";
    packets[1]="{\"type\":\"session.updated\",\"session\":{\"response_format\":\"mp3\"}}";
    packets[2]="{\"type\":\"response.audio.delta\",\"delta\":\"AQID\"}";
    packets[3]="{\"type\":\"response.audio.delta\",\"delta\":\"BAUG\"}";
    packets[4]="{\"type\":\"response.audio.done\"}";packets[5]="{\"type\":\"session.finished\"}";
}
int main(void) {
    muse_tts_config_t cfg={0};strcpy(cfg.key,"openai-test");strcpy(cfg.qwen_key,"qwen-test");
    strcpy(cfg.qwen_region,"singapore");strcpy(cfg.qwen_voice,"Cherry");strcpy(cfg.qwen_model,"qwen3-tts-flash-realtime");cfg.speed_pct=125;
    reset();handshake=101;assert(muse_qwen_fetch(&cfg,"你好",NULL,active,audio,status));
    assert(received==6&&!memcmp(collected,"\1\2\3\4\5\6",6)&&sends==4&&status_seen==101&&created==destroyed);
    reset();handshake=302;assert(!muse_qwen_fetch(&cfg,"你好",NULL,active,audio,status));
    assert(connects==1&&!received&&!sends&&status_seen==302&&created==destroyed);
    reset();handshake=101;packets[1]="{\"type\":\"session.updated\",\"session\":{\"response_format\":\"pcm\"}}";
    assert(!muse_qwen_fetch(&cfg,"你好",NULL,active,audio,status));assert(sends==1&&!received&&created==destroyed);
    reset();handshake=101;cancel_audio=true;assert(!muse_qwen_fetch(&cfg,"你好",NULL,active,audio,status));
    assert(received==3&&created==destroyed);
    reset();handshake=101;packets[2]="{\"type\":\"error\"}";assert(!muse_qwen_fetch(&cfg,"你好",NULL,active,audio,status));
    assert(!received&&created==destroyed);
    reset();handshake=101;alive=false;assert(!muse_qwen_fetch(&cfg,"你好",NULL,active,audio,status));
    assert(!connects&&created==destroyed);
}
'''

class QwenTransport(unittest.TestCase):
    def test_session_stream_cancellation_redirect_and_cleanup(self):
        src=ROOT/'components/muse';cj=ROOT/'managed_components/espressif__cjson/cJSON'
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'fake.h').write_text(FAKE);(p/'harness.c').write_text(HARNESS)
            for name in ('esp_err.h','esp_crt_bundle.h','esp_heap_caps.h','esp_log.h','esp_random.h',
                'esp_timer.h','esp_transport.h','esp_transport_ssl.h','esp_transport_ws.h'):
                (p/name).write_text('#include "fake.h"\n')
            (p/'mbedtls').mkdir();(p/'mbedtls/base64.h').write_text('#include "fake.h"\n')
            subprocess.run(['cc','-std=c11','-I'+str(p),'-I'+str(src),'-I'+str(cj),
                str(p/'harness.c'),str(src/'muse_qwen_tts.c'),str(src/'muse_qwen_payload.c'),
                str(src/'muse_tts_payload.c'),str(cj/'cJSON.c'),'-o',str(p/'test')],check=True)
            subprocess.run([str(p/'test')],check=True)
