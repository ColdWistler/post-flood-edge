/*
 * ThingsBoard transformation Script body for cloud/artifacts/wqi_rule_chain.json.
 *
 * Runs on every telemetry message from a node. The edge already publishes `wqi`
 * and `wqi_band`; this node stores its own values under `wqi_server` and
 * `wqi_band_server` so the authoritative edge values are never overwritten.
 * `server_received_ms` is the broker-side receipt clock used by `cloud.benchmark`
 * to measure end-to-end latency against the original acquisition `ts`.
 *
 * Task 2 Brown WQI, no-chlorine fallback:
 *   standards S = (8.5 pH, 5 NTU, 1000 ppm), ideals V0 = (7, 0, 0)
 *   weights  w = (0.3692, 0.6277, 0.0031)
 */

var ph = Number(msg.ph_25);
var turbidity = Number(msg.turbidity_ntu);
var tds = Number(msg.tds_ppm);

if (isFinite(ph) && isFinite(turbidity) && isFinite(tds)) {
    var qPh = Math.max(0.0, 100.0 * (ph - 7.0) / (8.5 - 7.0));
    var qTurbidity = Math.max(0.0, 100.0 * (turbidity - 0.0) / (5.0 - 0.0));
    var qTds = Math.max(0.0, 100.0 * (tds - 0.0) / (1000.0 - 0.0));

    var wqi = qPh * 0.3692 + qTurbidity * 0.6277 + qTds * 0.0031;
    var band;
    if (wqi <= 25.0) {
        band = "Excellent";
    } else if (wqi <= 50.0) {
        band = "Good";
    } else if (wqi <= 75.0) {
        band = "Poor";
    } else if (wqi <= 100.0) {
        band = "Very poor";
    } else {
        band = "Unsuitable";
    }

    msg.wqi_server = Math.round(wqi * 100.0) / 100.0;
    msg.wqi_band_server = band;
}

msg.server_received_ms = new Date().getTime();

return {msg: msg, metadata: metadata, msgType: msgType};
