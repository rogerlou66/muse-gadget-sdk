/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include "muse_settings.h"
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

typedef bool (*muse_qwen_active_cb)(void *ctx);
typedef bool (*muse_qwen_audio_cb)(void *ctx,const void *data,size_t len);
typedef void (*muse_qwen_status_cb)(void *ctx,int status);
bool muse_qwen_fetch(const muse_tts_config_t *cfg,const char *text,void *ctx,
    muse_qwen_active_cb active,muse_qwen_audio_cb audio,muse_qwen_status_cb status);
/* JSON constructors return cJSON-allocated strings; caller uses cJSON_free. */
char *muse_qwen_session(const char *voice,double speed,const char *event_id);
char *muse_qwen_text(const char *text,const char *event_id);
char *muse_qwen_command(const char *type,const char *event_id);
/* Accept a complete WebSocket text message. Returns event classification;
 * audio chunks remain Base64 until the transport worker consumes them. */
enum {QWEN_OTHER,QWEN_CREATED,QWEN_UPDATED,QWEN_AUDIO,QWEN_AUDIO_DONE,QWEN_FINISHED,QWEN_ERROR};
int muse_qwen_event(const char *json,char **audio);
