/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Bounded streaming decoder. Reads files only; never writes or formats them. */
typedef struct muse_music_file muse_music_file_t;
muse_music_file_t *muse_music_file_open(const char *path, int output_rate);
/* Mono frames: >0 audio, 0 clean end, -1 invalid file/read error. */
int muse_music_file_read(muse_music_file_t *file, int16_t *out, size_t frames);
const char *muse_music_file_error(const muse_music_file_t *file);
void muse_music_file_close(muse_music_file_t *file);
#ifdef __cplusplus
}
#endif
