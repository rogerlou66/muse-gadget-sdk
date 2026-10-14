/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#ifdef __cplusplus
extern "C" {
#endif
#define MUSE_ASR_TEXT_MAX 2048
#define MUSE_ASR_NO_SPEECH (-3)
#define MUSE_ASR_BOUNDARY "MuseASRFormBoundary_v1"
#define MUSE_ASR_TAIL "\r\n--" MUSE_ASR_BOUNDARY "--\r\n"
bool muse_asr_enabled(void);
uint32_t muse_asr_start(const void *wav, size_t len);
void muse_asr_cancel(uint32_t ticket);
/* 0 pending, 1 transcript, -1 failed, -2 cancelled, -3 no recognized speech.
 * Never returns old-turn text. Empty speech should be replaced, not retried. */
int muse_asr_poll(uint32_t ticket, char *text, size_t cap);
int muse_asr_last_http(void);
/* Optional diagnostic export of the last completed request's exact WAV, RAM only.
 * Returns an owned copy; caller frees it. No recording is written to flash. */
bool muse_asr_snapshot(uint8_t **wav, size_t *len);
int muse_asr_form(const char *model,const char *language,const char *prompt,char *out,size_t cap);
bool muse_asr_parse(const char *json,char *text,size_t cap);
bool muse_asr_text_empty(const char *json);
bool muse_asr_valid_wav(const uint8_t *wav,size_t len);
#ifdef __cplusplus
}
#endif
