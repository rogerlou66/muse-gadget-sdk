/* SPDX-License-Identifier: Apache-2.0 */
#include "muse_tts.h"
#include "muse_settings.h"
#include "muse_qwen_tts.h"
#include <stdatomic.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>
#include "cJSON.h"
#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/queue.h"
#include "freertos/stream_buffer.h"
#include "freertos/task.h"

#define AUDIO_BYTES (96 * 1024)
#define AUDIO_PREFILL (48 * 1024) /* about 3 s at the usual 128 kbps MP3 rate */
#define REQUEST_TIMEOUT_US (90 * 1000000LL)
static const char *TAG="muse_tts";
typedef struct { uint32_t ticket; muse_tts_config_t cfg; char text[1024]; } job_t;
typedef struct { uint32_t ticket; bool audio_type; } response_t;
static QueueHandle_t s_jobs;
static StreamBufferHandle_t s_audio;
static atomic_uint s_counter, s_ticket, s_owner;
static atomic_int s_state, s_http;
static atomic_bool s_prefilled;

static bool current(uint32_t ticket) {return atomic_load(&s_ticket)==ticket;}
static void free_job(job_t *job)
{
    if (job) {memset(&job->cfg,0,sizeof(job->cfg));memset(job->text,0,sizeof(job->text));free(job);}
}

static bool qwen_active(void *ctx) {return current(((job_t *)ctx)->ticket);}
static void qwen_status(void *ctx,int status) {if(qwen_active(ctx))atomic_store(&s_http,status);}
static bool qwen_audio(void *ctx,const void *data,size_t len)
{
    const uint8_t *p=data;
    int64_t deadline=esp_timer_get_time()+REQUEST_TIMEOUT_US;
    for(size_t off=0;off<len;) {
        if(!qwen_active(ctx) || esp_timer_get_time()>deadline)return false;
        off+=xStreamBufferSend(s_audio,p+off,len-off,pdMS_TO_TICKS(20));
    }
    return true;
}

static esp_err_t http_event(esp_http_client_event_t *e)
{
    response_t *r=e->user_data;
    if (e->event_id==HTTP_EVENT_ON_HEADER && !strcasecmp(e->header_key,"Content-Type")) {
        r->audio_type=!strncasecmp(e->header_value,"audio/mpeg",10)||
            !strncasecmp(e->header_value,"audio/mp3",9)||
            !strncasecmp(e->header_value,"application/octet-stream",24);
    }
    return ESP_OK;
}

static bool fetch(job_t *job)
{
    response_t response={.ticket=job->ticket};
    char *body=muse_tts_payload(job->text,job->cfg.model,job->cfg.voice,job->cfg.speed_pct/100.0);
    if (!body) return false;
    esp_http_client_config_t cfg={
        .url="https://api.openai.com/v1/audio/speech",
        .method=HTTP_METHOD_POST,
        .timeout_ms=15000,
        .buffer_size=4096,
        .buffer_size_tx=4096,
        .crt_bundle_attach=esp_crt_bundle_attach,
        .disable_auto_redirect=true, /* never forward the bearer key to a redirect */
        .event_handler=http_event,
        .user_data=&response,
    };
    esp_http_client_handle_t client=esp_http_client_init(&cfg);
    if (!client) {cJSON_free(body);return false;}
    char auth[MUSE_TTS_KEY_MAX+8];
    snprintf(auth,sizeof(auth),"Bearer %s",job->cfg.key);
    esp_http_client_set_header(client,"Authorization",auth);
    esp_http_client_set_header(client,"Content-Type","application/json");
    esp_http_client_set_header(client,"Accept","audio/mpeg");
    memset(auth,0,sizeof(auth));
    bool ok=false;
    size_t total=0;
    int64_t deadline=esp_timer_get_time()+REQUEST_TIMEOUT_US;
    if (!current(job->ticket) || esp_http_client_open(client,strlen(body))!=ESP_OK) goto cleanup;
    for (size_t off=0,n=strlen(body);off<n;) {
        if (!current(job->ticket)) goto cleanup;
        int sent=esp_http_client_write(client,body+off,n-off);
        if (sent<=0) goto cleanup;
        off+=sent;
    }
    esp_http_client_fetch_headers(client);
    int status=esp_http_client_get_status_code(client);
    if (current(job->ticket)) atomic_store(&s_http,status);
    if (status!=200 || !response.audio_type) goto cleanup;
    uint8_t buf[4096];
    while (current(job->ticket) && esp_timer_get_time()<deadline) {
        int n=esp_http_client_read(client,(char *)buf,sizeof(buf));
        if (n<0) goto cleanup;
        if (!n) {
            if (esp_http_client_is_complete_data_received(client)) {ok=total>0;break;}
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
        for (size_t off=0;off<(size_t)n;) {
            if (!current(job->ticket)||esp_timer_get_time()>=deadline) goto cleanup;
            off+=xStreamBufferSend(s_audio,buf+off,n-off,pdMS_TO_TICKS(20));
        }
        total+=n;
    }
cleanup:
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    cJSON_free(body);
    if (current(job->ticket)) ESP_LOGI(TAG,"cloud audio: HTTP %d, %u bytes, %s",
        atomic_load(&s_http),(unsigned)total,ok?"complete":"failed");
    return ok;
}

static void worker(void *arg)
{
    (void)arg;
    for (;;) {
        job_t *job;
        if (xQueueReceive(s_jobs,&job,portMAX_DELAY)!=pdTRUE) continue;
        if (current(job->ticket)) {
            /* Only this producer resets the buffer, after the previous fetch
             * has exited. Readers cannot see audio until its owner is set. */
            xStreamBufferReset(s_audio);
            atomic_store(&s_owner,job->ticket);
            bool ok=job->cfg.qwen?muse_qwen_fetch(&job->cfg,job->text,job,qwen_active,qwen_audio,qwen_status):fetch(job);
            if (current(job->ticket)) atomic_store(&s_state,ok?MUSE_TTS_DONE:MUSE_TTS_ERROR);
        }
        free_job(job);
    }
}

static bool init(void)
{
    if (s_jobs) return true;
    s_audio=xStreamBufferCreateWithCaps(AUDIO_BYTES,1,MALLOC_CAP_SPIRAM);
    s_jobs=xQueueCreateWithCaps(1,sizeof(job_t *),MALLOC_CAP_SPIRAM);
    if (!s_audio || !s_jobs || xTaskCreatePinnedToCoreWithCaps(worker,"muse_tts",24*1024,NULL,4,NULL,0,
        MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT)!=pdPASS) {
        if (s_audio) vStreamBufferDelete(s_audio);
        if (s_jobs) vQueueDelete(s_jobs);
        s_audio=NULL;s_jobs=NULL;
        ESP_LOGW(TAG,"worker allocation failed; captions only");
        return false;
    }
    /* The library's debug header logs must never include bearer credentials. */
    esp_log_level_set("HTTP_CLIENT",ESP_LOG_WARN);
    return true;
}

bool muse_tts_ready(void)
{
    return muse_settings_tts_on() && muse_settings_tts_configured();
}

uint32_t muse_tts_start(const char *text)
{
    if (!text || !text[0] || !muse_tts_ready() || !init()) return 0;
    job_t *job=heap_caps_calloc(1,sizeof(*job),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if (!job) return 0;
    muse_settings_tts_config(&job->cfg);
    if (!job->cfg.on || !(job->cfg.qwen?job->cfg.qwen_key[0]:job->cfg.key[0])) {free_job(job);return 0;}
    strlcpy(job->text,text,sizeof(job->text));
    job->ticket=atomic_fetch_add(&s_counter,1)+1;
    if (!job->ticket) job->ticket=atomic_fetch_add(&s_counter,1)+1;
    atomic_store(&s_owner,0);
    atomic_store(&s_ticket,job->ticket);
    atomic_store(&s_state,MUSE_TTS_ACTIVE);
    atomic_store(&s_prefilled,false);
    atomic_store(&s_http,0);
    job_t *stale;
    while (xQueueReceive(s_jobs,&stale,0)==pdTRUE) free_job(stale);
    uint32_t ticket=job->ticket;
    if (xQueueSend(s_jobs,&job,0)!=pdTRUE) {free_job(job);muse_tts_cancel(ticket);return 0;}
    return ticket;
}

void muse_tts_cancel(uint32_t ticket)
{
    if (ticket && current(ticket)) {
        atomic_store(&s_ticket,0);
        atomic_store(&s_owner,0);
        atomic_store(&s_state,MUSE_TTS_CANCELLED);
    }
}

size_t muse_tts_read(uint32_t ticket, void *out, size_t cap, int *state)
{
    *state=MUSE_TTS_ACTIVE;
    if (!ticket || !current(ticket)) {*state=MUSE_TTS_CANCELLED;return 0;}
    if (atomic_load(&s_owner)!=ticket) return 0;
    int status=atomic_load(&s_state);
    if(!atomic_load(&s_prefilled)) {
        size_t available=xStreamBufferBytesAvailable(s_audio);
        if(status==MUSE_TTS_ACTIVE && available<AUDIO_PREFILL) return 0;
        atomic_store(&s_prefilled,true);
        ESP_LOGI(TAG,"playback prefill: %u MP3 bytes",(unsigned)available);
    }
    size_t n=xStreamBufferReceive(s_audio,out,cap,0);
    status=atomic_load(&s_state);
    if (status==MUSE_TTS_ERROR || (status==MUSE_TTS_DONE && xStreamBufferBytesAvailable(s_audio)==0)) *state=status;
    return n;
}

int muse_tts_last_http(void) {return atomic_load(&s_http);}
