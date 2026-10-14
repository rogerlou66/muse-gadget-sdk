/* SPDX-License-Identifier: Apache-2.0 */
#include "muse_asr.h"
#include "muse_settings.h"
#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    uint32_t ticket;
    muse_asr_config_t cfg;
    char key[MUSE_TTS_KEY_MAX+1];
    uint8_t *wav;
    size_t len;
} job_t;
typedef struct {uint32_t ticket;bool ok,no_speech;char text[MUSE_ASR_TEXT_MAX];} result_t;
static QueueHandle_t s_jobs,s_results;
static SemaphoreHandle_t s_snapshot_lock;
static uint8_t *s_snapshot;
static size_t s_snapshot_len;
static atomic_uint s_counter,s_ticket;
static atomic_int s_http;
static const char *TAG="muse_asr";
static bool current(uint32_t t) {return atomic_load(&s_ticket)==t;}
static void free_job(job_t *j)
{
    if(j) {memset(j->key,0,sizeof(j->key));free(j->wav);free(j);}
}

static bool write_part(esp_http_client_handle_t client,job_t *job,const void *data,size_t n,int64_t deadline)
{
    const char *p=data;
    for(size_t off=0;off<n;) {
        if(!current(job->ticket)||esp_timer_get_time()>=deadline) return false;
        size_t left=n-off; if(left>4096) left=4096;
        int sent=esp_http_client_write(client,p+off,left);
        if(sent<=0) return false;
        off+=sent;
    }
    return true;
}

static bool fetch(job_t *job,result_t *result)
{
    char prefix[1024];
    int head=muse_asr_form(job->cfg.model,job->cfg.language,job->cfg.prompt,prefix,sizeof(prefix));
    if(head<0) return false;
    esp_http_client_config_t cfg={
        .url="https://api.openai.com/v1/audio/transcriptions",
        .method=HTTP_METHOD_POST,.timeout_ms=15000,.buffer_size=4096,.buffer_size_tx=4096,
        .crt_bundle_attach=esp_crt_bundle_attach,.disable_auto_redirect=true,
    };
    esp_http_client_handle_t c=esp_http_client_init(&cfg);
    if(!c) return false;
    char auth[MUSE_TTS_KEY_MAX+8];
    snprintf(auth,sizeof(auth),"Bearer %s",job->key);
    esp_http_client_set_header(c,"Authorization",auth);
    memset(auth,0,sizeof(auth));
    esp_http_client_set_header(c,"Content-Type","multipart/form-data; boundary=" MUSE_ASR_BOUNDARY);
    esp_http_client_set_header(c,"Accept","application/json");
    int64_t deadline=esp_timer_get_time()+90*1000000LL;
    bool ok=false;
    char response[8192];
    size_t n=0;
    if(!current(job->ticket)||esp_http_client_open(c,head+job->len+sizeof(MUSE_ASR_TAIL)-1)!=ESP_OK) goto cleanup;
    if(!write_part(c,job,prefix,head,deadline)||!write_part(c,job,job->wav,job->len,deadline)||
        !write_part(c,job,MUSE_ASR_TAIL,sizeof(MUSE_ASR_TAIL)-1,deadline)) goto cleanup;
    esp_http_client_fetch_headers(c);
    int status=esp_http_client_get_status_code(c);
    if(current(job->ticket)) atomic_store(&s_http,status);
    if(status!=200) goto cleanup; /* never log service bodies or credentials */
    while(current(job->ticket)&&esp_timer_get_time()<deadline&&n<sizeof(response)-1) {
        int got=esp_http_client_read(c,response+n,sizeof(response)-1-n);
        if(got<0) goto cleanup;
        n+=got;
        if(esp_http_client_is_complete_data_received(c)) {
            response[n]=0;
            ok=muse_asr_parse(response,result->text,sizeof(result->text));
            if(!ok)result->no_speech=muse_asr_text_empty(response);
            break;
        }
        if(!got) vTaskDelay(pdMS_TO_TICKS(10));
    }
cleanup:
    esp_http_client_close(c);esp_http_client_cleanup(c);
    if(current(job->ticket)) ESP_LOGI(TAG,"transcription: HTTP %d, %.2fs WAV, %s (%u text bytes)",
        atomic_load(&s_http),(job->len-44)/32000.0,ok?"complete":result->no_speech?"no speech":"failed",(unsigned)strlen(result->text));
    return ok;
}

static void worker(void *arg)
{
    (void)arg;
    for(;;) {
        job_t *j;
        if(xQueueReceive(s_jobs,&j,portMAX_DELAY)!=pdTRUE) continue;
        result_t *r=heap_caps_calloc(1,sizeof(*r),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
        if(current(j->ticket)) {
            if(r) {r->ticket=j->ticket;r->ok=fetch(j,r);}
            if(current(j->ticket)) {
                xSemaphoreTake(s_snapshot_lock,portMAX_DELAY);
                free(s_snapshot);s_snapshot=j->wav;s_snapshot_len=j->len;j->wav=NULL;
                xSemaphoreGive(s_snapshot_lock);
                if(r) {
                    result_t *stale;
                    while(xQueueReceive(s_results,&stale,0)==pdTRUE) free(stale);
                    if(xQueueSend(s_results,&r,0)==pdTRUE) r=NULL;
                } else {
                    /* Allocation failure must terminate, rather than strand the turn. */
                    unsigned expected=j->ticket;
                    atomic_compare_exchange_strong(&s_ticket,&expected,0);
                }
            }
        }
        free(r);free_job(j);
    }
}

static bool init(void)
{
    if(s_jobs) return true;
    s_snapshot_lock=xSemaphoreCreateMutex();
    s_jobs=xQueueCreateWithCaps(1,sizeof(job_t *),MALLOC_CAP_SPIRAM);
    s_results=xQueueCreateWithCaps(1,sizeof(result_t *),MALLOC_CAP_SPIRAM);
    if(!s_jobs||!s_results||!s_snapshot_lock||xTaskCreatePinnedToCoreWithCaps(worker,"muse_asr",32*1024,NULL,4,NULL,0,
        MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT)!=pdPASS) {
        if(s_jobs)vQueueDelete(s_jobs);
        if(s_results)vQueueDelete(s_results);
        if(s_snapshot_lock)vSemaphoreDelete(s_snapshot_lock);
        s_jobs=s_results=NULL;s_snapshot_lock=NULL;
        return false;
    }
    esp_log_level_set("HTTP_CLIENT",ESP_LOG_WARN);
    return true;
}

bool muse_asr_enabled(void)
{
    muse_asr_config_t cfg;muse_settings_asr_config(&cfg);return cfg.on;
}

uint32_t muse_asr_start(const void *wav,size_t len)
{
    if(!muse_asr_valid_wav(wav,len)||!muse_asr_enabled()||!init()) return 0;
    job_t *j=heap_caps_calloc(1,sizeof(*j),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if(!j)return 0;
    muse_settings_asr_config(&j->cfg);
    muse_tts_config_t keycfg;muse_settings_tts_config(&keycfg);
    memcpy(j->key,keycfg.key,sizeof(j->key));memset(&keycfg,0,sizeof(keycfg));
    if(!j->key[0]||!j->cfg.on) {free_job(j);return 0;}
    j->wav=heap_caps_malloc(len,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if(!j->wav) {free_job(j);return 0;}
    memcpy(j->wav,wav,len);j->len=len;
    j->ticket=atomic_fetch_add(&s_counter,1)+1;
    if(!j->ticket)j->ticket=atomic_fetch_add(&s_counter,1)+1;
    atomic_store(&s_ticket,j->ticket);atomic_store(&s_http,0);
    job_t *stale;while(xQueueReceive(s_jobs,&stale,0)==pdTRUE)free_job(stale);
    result_t *old;while(xQueueReceive(s_results,&old,0)==pdTRUE)free(old);
    uint32_t t=j->ticket;
    if(xQueueSend(s_jobs,&j,0)!=pdTRUE) {free_job(j);muse_asr_cancel(t);return 0;}
    return t;
}

void muse_asr_cancel(uint32_t t) {if(t&&current(t))atomic_store(&s_ticket,0);}
int muse_asr_poll(uint32_t t,char *text,size_t cap)
{
    if(!t||!current(t))return -2;
    result_t *r;
    while(xQueueReceive(s_results,&r,0)==pdTRUE) {
        bool match=r->ticket==t;
        bool ok=r->ok&&text&&strlen(r->text)<cap;
        int status=r->no_speech?MUSE_ASR_NO_SPEECH:ok?1:-1;
        if(match&&ok)strlcpy(text,r->text,cap);
        free(r);
        if(match) return status;
    }
    return 0;
}
int muse_asr_last_http(void) {return atomic_load(&s_http);}
bool muse_asr_snapshot(uint8_t **wav,size_t *len)
{
    *wav=NULL;*len=0;
    if(!s_snapshot_lock)return false;
    xSemaphoreTake(s_snapshot_lock,portMAX_DELAY);
    if(s_snapshot) {
        *wav=heap_caps_malloc(s_snapshot_len,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
        if(*wav){memcpy(*wav,s_snapshot,s_snapshot_len);*len=s_snapshot_len;}
    }
    xSemaphoreGive(s_snapshot_lock);
    return *wav!=NULL;
}
