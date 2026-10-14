#pragma once
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif
enum { MUSE_TTS_ACTIVE=0, MUSE_TTS_DONE=1, MUSE_TTS_ERROR=-1, MUSE_TTS_CANCELLED=-2 };
bool muse_tts_ready(void);
uint32_t muse_tts_start(const char *text);
void muse_tts_cancel(uint32_t ticket);
/* Nonblocking; terminal DONE is returned only after the last audio byte. */
size_t muse_tts_read(uint32_t ticket, void *out, size_t cap, int *state);
int muse_tts_last_http(void);
char *muse_tts_payload(const char *text, const char *model, const char *voice, double speed);
size_t muse_tts_utf8_prefix(const char *text);
#ifdef __cplusplus
}
#endif
