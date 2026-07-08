#include <WiFi.h>

// Channel center frequencies in MHz
const int channelFreq[] = {
    2412, 2417, 2422, 2427, 2432,
    2437, 2442, 2447, 2452, 2457,
    2462, 2467, 2472
};

float dbmToWatts(int dbm) {
    return pow(10.0, dbm / 10.0) * 1e-3;  // convert dBm -> mW -> W
}

String formatPower(int dbm) {
    float W = dbmToWatts(dbm);
    char buf[20];
    if (W >= 1e-3) {
        sprintf(buf, "%.3f mW", W * 1e3);
    } else if (W >= 1e-6) {
        sprintf(buf, "%.3f uW", W * 1e6);
    } else if (W >= 1e-9) {
        sprintf(buf, "%.3f nW", W * 1e9);
    } else {
        sprintf(buf, "%.3f pW", W * 1e12);
    }
    return String(buf);
}

void setup() {
    Serial.begin(115200);
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();
    delay(100);
    Serial.println("ESP32 2.4GHz Channel Scanner");
    Serial.println("Ch | Freq (MHz) | Networks | Strongest dBm | Power");
    Serial.println("---|-----------|----------|---------------|------");
}

void loop() {
    Serial.println("\n--- Sweep ---");

    for (int ch = 1; ch <= 13; ch++) {
        int n = WiFi.scanNetworks(false, true, false, 200, ch);

        int strongestRSSI = -999;
        if (n > 0) {
            for (int i = 0; i < n; i++) {
                if (WiFi.RSSI(i) > strongestRSSI)
                    strongestRSSI = WiFi.RSSI(i);
            }
        }

        Serial.printf("%2d | %4d MHz  | %3d      | ", ch, channelFreq[ch - 1], max(n, 0));
        if (strongestRSSI > -999) {
            Serial.printf("%4d dBm      | %s\n", strongestRSSI, formatPower(strongestRSSI).c_str());
        } else {
            Serial.printf("  (nothing)    | --\n");
        }

        WiFi.scanDelete();
    }

    delay(2000);
}