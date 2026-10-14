/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include "esp_err.h"

/* Voice-task owned; call only after closing the previous music file. */
esp_err_t muse_box0_storage_mount(const char *path);
