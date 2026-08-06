/*
 * WifiIdentification — ESP32-S3 CSI Receiver
 *
 * Architecture:
 *   - Promiscuous mode ON → captures real sender MACs in CSI callback
 *   - Filters ONLY packets from TRANSMITTER_MAC
 *   - Collects CSI over BURST_WINDOW_MS (200ms)
 *   - HTTP POSTs each burst to server REST API at POST /api/csi
 *
 * Key change from WaveSense: WebSocket replaced by HTTP POST.
 * Server is the brain — it performs feature extraction, ML inference,
 * people counting, and serves the dashboard.
 */
#include "esp_eap_client.h"
#include <stdio.h>
#include <string.h>
#include <math.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "esp_http_client.h"
#include "lwip/sockets.h"

/* ── Configuration ── */
#define WIFI_SSID           "CDOT-WIFI"

/* Lock to same AP as transmitter (channel 9) */
#define TARGET_AP_BSSID     {0x00, 0x24, 0x88, 0xA0, 0x50, 0x3B}

/* Dynamic Server Target (loaded from NVS / Auto-discovered via UDP) */
static char current_server_ip[64] = "172.18.5.11";
static char server_url[128] = "http://172.18.5.11:8080/api/csi";
static portMUX_TYPE server_ip_spinlock = portMUX_INITIALIZER_UNLOCKED;
static bool server_url_changed = false;


/* Receiver identity (used in API payload) */
#define RECEIVER_ID         "rx01"

/* Capture CSI from C-DOT WiFi AP (BSSID 00:24:88:A0:50:3B).
 * The AP IS the signal source — no separate transmitter ESP32 needed.
 * AP sends beacon frames every ~100ms = 10 CSI samples/sec baseline.
 * Set all bytes to 0 to accept CSI from ALL senders (promiscuous). */
static const uint8_t AP_SOURCE_MAC[6] = {0x00, 0x24, 0x88, 0xA0, 0x50, 0x3B};
#define FILTER_BY_AP 1   // set 0 to capture from ALL senders

/* Burst window: collect packets for 200ms, then send one HTTP POST */
#define BURST_WINDOW_MS     200

static const char *TAG = "WIFI_ID_RX";
static EventGroupHandle_t wifi_event_group;
const int WIFI_CONNECTED_BIT = BIT0;
static uint8_t ap_bssid[6] = {0};

/* ── Measurement message (queue payload) ── */
typedef struct {
    float    rssi_mean;
    int      packet_count;
    char     csi_array_str[4096];
    char     tx_mac[18];
    uint32_t timestamp_ms;
} csi_measurement_t;

static QueueHandle_t csi_queue = NULL;

/* ── Burst accumulators ── */
static uint32_t burst_start_ms  = 0;
static long     rssi_sum        = 0;
static int      pkt_count       = 0;

/* Latest CSI buf for the burst (we store last packet's CSI) */
static int8_t   last_csi_buf[512];
static int      last_csi_len    = 0;
static uint8_t  last_mac[6]     = {0};

/* ── NVS & Auto-Discovery Helpers ── */
static void update_server_url(const char *new_ip)
{
    taskENTER_CRITICAL(&server_ip_spinlock);
    snprintf(current_server_ip, sizeof(current_server_ip), "%s", new_ip);
    snprintf(server_url, sizeof(server_url), "http://%s:8080/api/csi", new_ip);
    server_url_changed = true;
    taskEXIT_CRITICAL(&server_ip_spinlock);
}

static void save_ip_to_nvs(const char *ip)
{
    nvs_handle_t h;
    if (nvs_open("storage", NVS_READWRITE, &h) == ESP_OK) {
        nvs_set_str(h, "server_ip", ip);
        nvs_commit(h);
        nvs_close(h);
        ESP_LOGI(TAG, "Saved new server IP to NVS: %s", ip);
    }
}

static void load_ip_from_nvs(void)
{
    nvs_handle_t h;
    if (nvs_open("storage", NVS_READONLY, &h) == ESP_OK) {
        char saved_ip[64] = {0};
        size_t len = sizeof(saved_ip);
        if (nvs_get_str(h, "server_ip", saved_ip, &len) == ESP_OK && strlen(saved_ip) > 0) {
            update_server_url(saved_ip);
            ESP_LOGI(TAG, "Loaded server IP from NVS: %s", saved_ip);
        }
        nvs_close(h);
    }
}

static void udp_beacon_task(void *pvParameters)
{
    xEventGroupWaitBits(wifi_event_group, WIFI_CONNECTED_BIT, pdFALSE, pdTRUE, portMAX_DELAY);

    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (sock < 0) {
        ESP_LOGE(TAG, "Failed to create UDP socket");
        vTaskDelete(NULL);
        return;
    }

    struct sockaddr_in rx_addr;
    memset(&rx_addr, 0, sizeof(rx_addr));
    rx_addr.sin_family = AF_INET;
    rx_addr.sin_port = htons(8089);
    rx_addr.sin_addr.s_addr = htonl(INADDR_ANY);

    if (bind(sock, (struct sockaddr *)&rx_addr, sizeof(rx_addr)) < 0) {
        ESP_LOGE(TAG, "Failed to bind UDP socket to port 8089");
        close(sock);
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "UDP Auto-Discovery listener active on port 8089");
    char rx_buf[128];

    while (1) {
        struct sockaddr_in src_addr;
        socklen_t addr_len = sizeof(src_addr);
        int len = recvfrom(sock, rx_buf, sizeof(rx_buf) - 1, 0, (struct sockaddr *)&src_addr, &addr_len);
        if (len <= 0) {
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }

        rx_buf[len] = '\0';
        if (strncmp(rx_buf, "SERVER_BEACON:", 14) == 0) {
            char *ip_str = rx_buf + 14;
            char *colon = strchr(ip_str, ':');
            if (colon) *colon = '\0';

            if (strlen(ip_str) > 0 && strcmp(ip_str, current_server_ip) != 0) {
                ESP_LOGI(TAG, "✨ Auto-discovered new server IP change: %s -> %s", current_server_ip, ip_str);
                update_server_url(ip_str);
                save_ip_to_nvs(ip_str);
            }
        }
    }
    close(sock);
    vTaskDelete(NULL);
}

/* ── HTTP event handler (required by esp_http_client) ── */
static esp_err_t http_event_handler(esp_http_client_event_t *evt)
{
    (void)evt;
    return ESP_OK;
}

/* ── POST CSI to server ── */
static void post_csi_task(void *pvParameters)
{
    static csi_measurement_t msg;
    char active_url[128];

    taskENTER_CRITICAL(&server_ip_spinlock);
    snprintf(active_url, sizeof(active_url), "%s", server_url);
    server_url_changed = false;
    taskEXIT_CRITICAL(&server_ip_spinlock);

    ESP_LOGI(TAG, "HTTP POST task started → %s", active_url);

    esp_http_client_config_t config = {
        .url             = active_url,
        .method          = HTTP_METHOD_POST,
        .event_handler   = http_event_handler,
        .timeout_ms      = 3000,
        .buffer_size     = 512,
        .buffer_size_tx  = 5120,
        .keep_alive_enable = false,
    };
    esp_http_client_handle_t client = NULL;
    int consecutive_failures = 0;

    while (1) {
        if (xQueueReceive(csi_queue, &msg, portMAX_DELAY) != pdTRUE) continue;

        bool url_changed = false;
        taskENTER_CRITICAL(&server_ip_spinlock);
        if (server_url_changed) {
            snprintf(active_url, sizeof(active_url), "%s", server_url);
            server_url_changed = false;
            url_changed = true;
        }
        taskEXIT_CRITICAL(&server_ip_spinlock);

        if (url_changed && client != NULL) {
            esp_http_client_cleanup(client);
            client = NULL;
            ESP_LOGI(TAG, "HTTP client target URL updated → %s", active_url);
        }

        if (client == NULL) {
            config.url = active_url;
            client = esp_http_client_init(&config);
        }

        /* Build JSON payload */
        static char payload[5000];
        int n = snprintf(payload, sizeof(payload),
            "{"
            "\"receiver_id\":\"%s\","
            "\"tx_mac\":\"%s\","
            "\"rssi\":%.2f,"
            "\"packet_count\":%d,"
            "\"timestamp_ms\":%lu,"
            "\"csi\":[%s]"
            "}",
            RECEIVER_ID,
            msg.tx_mac,
            msg.rssi_mean,
            msg.packet_count,
            (unsigned long)msg.timestamp_ms,
            msg.csi_array_str
        );

        if (n <= 0 || n >= (int)sizeof(payload)) {
            ESP_LOGW(TAG, "Payload too large, skipping");
            continue;
        }

        esp_http_client_set_post_field(client, payload, strlen(payload));
        esp_http_client_set_header(client, "Content-Type", "application/json");
        esp_http_client_set_header(client, "Connection", "close");

        // Temporarily pause promiscuous mode so STA TCP stack can send HTTP POST & receive ACK
        esp_wifi_set_promiscuous(false);
        esp_err_t err = esp_http_client_perform(client);
        esp_wifi_set_promiscuous(true);

        if (err == ESP_OK) {
            int status = esp_http_client_get_status_code(client);
            if (status != 200 && status != 201) {
                ESP_LOGW(TAG, "Server returned HTTP %d", status);
                consecutive_failures++;
            } else {
                consecutive_failures = 0; // Reset counter on successful transmission
            }
            esp_http_client_cleanup(client);
            client = NULL;
        } else {
            ESP_LOGE(TAG, "HTTP POST failed: %s", esp_err_to_name(err));
            esp_http_client_cleanup(client);
            client = NULL;
            consecutive_failures++;
        }

        /* Watchdog: If connection is broken, force a clean WiFi reconnect */
        if (consecutive_failures >= 5) {
            ESP_LOGW(TAG, "Watchdog: %d consecutive HTTP failures. Restarting WiFi connection...", consecutive_failures);
            
            // Disable promiscuous mode so STA stack can perform handshake normally
            esp_wifi_set_promiscuous(false);
            xEventGroupClearBits(wifi_event_group, WIFI_CONNECTED_BIT);
            
            esp_wifi_disconnect();
            esp_wifi_connect();
            
            // Block until reconnection completes and new IP is acquired
            xEventGroupWaitBits(wifi_event_group, WIFI_CONNECTED_BIT, pdFALSE, pdTRUE, portMAX_DELAY);
            
            // Restore promiscuous mode for CSI capture
            esp_wifi_set_promiscuous(true);
            consecutive_failures = 0;
            ESP_LOGI(TAG, "Watchdog: WiFi reconnected successfully. Promiscuous mode restored.");
        }
    }
    if (client) esp_http_client_cleanup(client);
}

/* ── CSI Callback (runs in WiFi task context) ── */
static void wifi_csi_rx_cb(void *ctx, wifi_csi_info_t *info)
{
    if (!info || !info->buf || info->len <= 0) return;

    /* Filter: only accept CSI from the C-DOT AP (or any sender if FILTER_BY_AP=0) */
#if FILTER_BY_AP
    if (ap_bssid[0] != 0 || ap_bssid[1] != 0 || ap_bssid[2] != 0 ||
        ap_bssid[3] != 0 || ap_bssid[4] != 0 || ap_bssid[5] != 0) {
        if (memcmp(info->mac, ap_bssid, 6) != 0) return;
    } else {
        if (memcmp(info->mac, AP_SOURCE_MAC, 6) != 0) return;
    }
#endif

    int8_t rssi = info->rx_ctrl.rssi;
    if (rssi == 0) return;

    uint32_t now = xTaskGetTickCount() * portTICK_PERIOD_MS;
    if (burst_start_ms == 0) burst_start_ms = now;

    rssi_sum += rssi;
    pkt_count++;

    /* Keep last CSI buffer */
    int copy_len = info->len < (int)sizeof(last_csi_buf) ? info->len : (int)sizeof(last_csi_buf);
    memcpy(last_csi_buf, info->buf, copy_len);
    last_csi_len = copy_len;
    memcpy(last_mac, info->mac, 6);

    /* End of burst window → enqueue measurement */
    if (now - burst_start_ms >= BURST_WINDOW_MS) {
        if (csi_queue == NULL) goto reset;

        static csi_measurement_t msg;
        memset(&msg, 0, sizeof(msg));
        msg.rssi_mean    = (float)rssi_sum / pkt_count;
        msg.packet_count = pkt_count;
        msg.timestamp_ms = now;

        snprintf(msg.tx_mac, sizeof(msg.tx_mac), "%02X:%02X:%02X:%02X:%02X:%02X",
                 last_mac[0], last_mac[1], last_mac[2],
                 last_mac[3], last_mac[4], last_mac[5]);

        /* Serialise CSI array */
        int offset = 0;
        int8_t *csi = (int8_t *)last_csi_buf;
        for (int i = 0; i < last_csi_len; i++) {
            if (offset >= (int)sizeof(msg.csi_array_str) - 8) break;
            offset += snprintf(msg.csi_array_str + offset,
                               sizeof(msg.csi_array_str) - offset,
                               "%d%s", csi[i], (i == last_csi_len - 1) ? "" : ",");
        }

        /* Log one line to serial for debugging */
        ESP_LOGI(TAG, "CSI burst: rssi=%.1f pkt=%d csi_len=%d",
                 msg.rssi_mean, msg.packet_count, last_csi_len);

        xQueueSend(csi_queue, &msg, 0);

reset:
        burst_start_ms = 0;
        rssi_sum       = 0;
        pkt_count      = 0;
        last_csi_len   = 0;
    }
}

/* ── WiFi Event Handler ── */
static void event_handler(void *arg, esp_event_base_t event_base,
                           int32_t event_id, void *event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGI(TAG, "Disconnected — retrying...");
        esp_wifi_connect();
        xEventGroupClearBits(wifi_event_group, WIFI_CONNECTED_BIT);
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "Got IP: " IPSTR, IP2STR(&event->ip_info.ip));
        wifi_ap_record_t ap;
        if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
            memcpy(ap_bssid, ap.bssid, 6);
            ESP_LOGI(TAG, "AP BSSID: %02x:%02x:%02x:%02x:%02x:%02x ch=%d",
                     ap_bssid[0], ap_bssid[1], ap_bssid[2],
                     ap_bssid[3], ap_bssid[4], ap_bssid[5], ap.primary);
        }
        xEventGroupSetBits(wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

/* ── Entry Point ── */
void app_main(void)
{
    ESP_ERROR_CHECK(nvs_flash_init());
    load_ip_from_nvs();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    wifi_event_group = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &event_handler, NULL));

    /* Create HTTP POST queue (size 3 — avoids OOM during reconnects) */
    csi_queue = xQueueCreate(3, sizeof(csi_measurement_t));
    if (!csi_queue) {
        ESP_LOGE(TAG, "Failed to create queue");
        return;
    }
    xTaskCreate(post_csi_task, "post_task", 8192, NULL, 5, NULL);
    xTaskCreate(udp_beacon_task, "udp_beacon_task", 4096, NULL, 4, NULL);

    /* WiFi config — locked to AP BSSID */
    uint8_t bssid[6] = TARGET_AP_BSSID;
    wifi_config_t wifi_config = {
        .sta = {
            .ssid      = WIFI_SSID,
            .bssid_set = true,
            .threshold.authmode = WIFI_AUTH_WPA2_ENTERPRISE,
        },
    };
    memcpy(wifi_config.sta.bssid, bssid, 6);

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));

    /* WPA2 Enterprise */
    ESP_ERROR_CHECK(esp_eap_client_set_identity(
        (const uint8_t *)"temp_kmg_intern1", strlen("temp_kmg_intern1")));
    ESP_ERROR_CHECK(esp_eap_client_set_username(
        (const uint8_t *)"temp_kmg_intern1", strlen("temp_kmg_intern1")));
    ESP_ERROR_CHECK(esp_eap_client_set_password(
        (const uint8_t *)"LKiPb#3i", strlen("LKiPb#3i")));
    ESP_ERROR_CHECK(esp_eap_client_set_ttls_phase2_method(ESP_EAP_TTLS_PHASE2_MSCHAPV2));
    ESP_ERROR_CHECK(esp_wifi_sta_enterprise_enable());
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "========================================");
    ESP_LOGI(TAG, "  WifiIdentification — RX Starting...");
    ESP_LOGI(TAG, "========================================");
    ESP_LOGI(TAG, "Connecting to %s ...", WIFI_SSID);

    xEventGroupWaitBits(wifi_event_group, WIFI_CONNECTED_BIT,
                        pdFALSE, pdTRUE, portMAX_DELAY);

    /* Configure and enable CSI */
    wifi_csi_config_t csi_cfg = {
        .lltf_en         = 1,
        .htltf_en        = 1,
        .stbc_htltf2_en  = 1,
        .ltf_merge_en    = 1,
        .channel_filter_en = 0,
        .manu_scale      = 0,
    };
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_cfg));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(&wifi_csi_rx_cb, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));

    /* Promiscuous mode: exposes real sender MAC in CSI callback */
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));

    ESP_LOGI(TAG, "CSI enabled | Promiscuous ON");
    ESP_LOGI(TAG, "Signal source: C-DOT AP %02X:%02X:%02X:%02X:%02X:%02X (no TX ESP32 needed)",
             AP_SOURCE_MAC[0], AP_SOURCE_MAC[1], AP_SOURCE_MAC[2],
             AP_SOURCE_MAC[3], AP_SOURCE_MAC[4], AP_SOURCE_MAC[5]);
    ESP_LOGI(TAG, "Posting bursts to %s", server_url);
}
