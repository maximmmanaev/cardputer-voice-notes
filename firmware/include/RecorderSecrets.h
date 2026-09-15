#pragma once

#if __has_include("local_config.h")
#include "local_config.h"
#endif

#ifndef RECORDER_WIFI_SSID
#define RECORDER_WIFI_SSID ""
#endif
#ifndef RECORDER_WIFI_PASSWORD
#define RECORDER_WIFI_PASSWORD ""
#endif
#ifndef RECORDER_DEVICE_TOKEN
#define RECORDER_DEVICE_TOKEN ""
#endif
#ifndef RECORDER_SYNC_FALLBACK_IP
#define RECORDER_SYNC_FALLBACK_IP ""
#endif
#ifndef RECORDER_SYNC_PORT
#define RECORDER_SYNC_PORT 8765
#endif
