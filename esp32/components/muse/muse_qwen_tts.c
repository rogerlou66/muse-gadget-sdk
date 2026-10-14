/* SPDX-License-Identifier: Apache-2.0 */
#include "muse_qwen_tts.h"
#include "muse_qwen_frame.h"
#include "cJSON.h"
#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_transport.h"
#include "esp_transport_ssl.h"
#include "esp_transport_ws.h"
#include "mbedtls/base64.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MESSAGE_CAP (64*1024)
static const char *TAG="muse_qwen";

static void event_id(char id[37])
{
    uint8_t b[16];esp_fill_random(b,sizeof(b));b[6]=(b[6]&15)|64;b[8]=(b[8]&63)|128;
    snprintf(id,37,"%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
        b[0],b[1],b[2],b[3],b[4],b[5],b[6],b[7],b[8],b[9],b[10],b[11],b[12],b[13],b[14],b[15]);
}
static bool send_json(esp_transport_handle_t ws,char *body)
{
    if(!body)return false;
    size_t n=strlen(body);
    int sent=esp_transport_ws_send_raw(ws,WS_TRANSPORT_OPCODES_TEXT|WS_TRANSPORT_OPCODES_FIN,body,n,15000);
    cJSON_free(body);return sent==(int)n;
}

bool muse_qwen_fetch(const muse_tts_config_t *cfg,const char *text,void *ctx,
    muse_qwen_active_cb active,muse_qwen_audio_cb audio,muse_qwen_status_cb status)
{
    const char *host=!strcmp(cfg->qwen_region,"singapore")?"dashscope-intl.aliyuncs.com":
        !strcmp(cfg->qwen_region,"beijing")?"dashscope.aliyuncs.com":NULL;
    if(!host || !cfg->qwen_key[0])return false;
    /* No additional RTOS stack: synchronous transport runs on the existing
     * TTS worker in PSRAM, with standard TLS/WS handshake verification. */
    esp_transport_handle_t ssl=esp_transport_ssl_init();
    esp_transport_handle_t ws=ssl?esp_transport_ws_init(ssl):NULL;
    char *message=heap_caps_malloc(MESSAGE_CAP,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    uint8_t *decoded=heap_caps_malloc(MESSAGE_CAP*3/4,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    bool ok=false,configured=false,submitted=false,audio_done=false,finish_sent=false;
    size_t total=0;
    char path[160],auth[MUSE_TTS_KEY_MAX+10],id[37],chunk[4096];
    muse_qwen_frame_t frame={0};
    int64_t deadline=esp_timer_get_time()+90*1000000LL;
    if(!ssl || !ws || !message || !decoded)goto cleanup;
    snprintf(path,sizeof(path),"/api-ws/v1/realtime?model=%s",cfg->qwen_model);
    snprintf(auth,sizeof(auth),"Bearer %s",cfg->qwen_key);
    esp_transport_ssl_crt_bundle_attach(ssl,esp_crt_bundle_attach);
    esp_transport_ws_set_path(ws,path);
    esp_transport_ws_config_t options={.ws_path=path,.auth=auth,.propagate_control_frames=false};
    if(esp_transport_ws_set_config(ws,&options)!=ESP_OK)goto cleanup;
    memset(auth,0,sizeof(auth));
    esp_log_level_set("TRANSPORT_WS",ESP_LOG_WARN); /* never debug-log authorization headers */
    if(!active(ctx))goto cleanup;
    int connected=esp_transport_connect(ws,host,443,15000);
    int http=esp_transport_ws_get_upgrade_request_status(ws);
    if(active(ctx))status(ctx,http);
    if(connected<0 || http!=101)goto cleanup; /* never follow a redirect with credentials */
    while(active(ctx) && esp_timer_get_time()<deadline) {
        int n=esp_transport_read(ws,chunk,sizeof(chunk),250);
        int opcode=esp_transport_ws_get_read_opcode(ws);
        if(n<0 || opcode==WS_TRANSPORT_OPCODES_CLOSE) {ok=audio_done&&total>0;break;}
        if(!n)continue;
        if(opcode!=WS_TRANSPORT_OPCODES_TEXT && opcode!=WS_TRANSPORT_OPCODES_CONT)goto cleanup;
        int state=muse_qwen_frame_add(&frame,opcode,esp_transport_ws_get_fin_flag(ws),
            esp_transport_ws_get_read_payload_len(ws),chunk,n,message,MESSAGE_CAP);
        if(state<0)goto cleanup;
        if(!state)continue;
        char *encoded=NULL;
        int type=muse_qwen_event(message,&encoded);
        if(type==QWEN_CREATED && !configured) {
            event_id(id);
            if(!send_json(ws,muse_qwen_session(cfg->qwen_voice,cfg->speed_pct/100.0,id)))goto cleanup;
            configured=true;
        } else if(type==QWEN_UPDATED && configured && !submitted) {
            event_id(id);if(!send_json(ws,muse_qwen_text(text,id)))goto cleanup;
            event_id(id);if(!send_json(ws,muse_qwen_command("input_text_buffer.commit",id)))goto cleanup;
            submitted=true;
        } else if(type==QWEN_AUDIO && submitted && !audio_done) {
            size_t bytes=0;
            int err=mbedtls_base64_decode(decoded,MESSAGE_CAP*3/4,&bytes,(const unsigned char *)encoded,strlen(encoded));
            cJSON_free(encoded);encoded=NULL;
            if(err || !bytes || !audio(ctx,decoded,bytes))goto cleanup;
            total+=bytes;
        } else if(type==QWEN_AUDIO_DONE && submitted) {
            audio_done=true;
            if(!finish_sent) {
                event_id(id);if(!send_json(ws,muse_qwen_command("session.finish",id)))goto cleanup;
                finish_sent=true;
            }
        } else if(type==QWEN_FINISHED) {ok=audio_done&&total>0;break;}
        else if(type==QWEN_ERROR) {ESP_LOGW(TAG,"service rejected speech session");goto cleanup;}
        cJSON_free(encoded);
    }
cleanup:
    memset(auth,0,sizeof(auth));
    if(ws) {esp_transport_close(ws);esp_transport_destroy(ws);}
    if(ssl)esp_transport_destroy(ssl);
    free(message);free(decoded);
    if(active(ctx))ESP_LOGI(TAG,"Qwen audio: %u MP3 bytes, %s",(unsigned)total,ok?"complete":"failed");
    return ok;
}
