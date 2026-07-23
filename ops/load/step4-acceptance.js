import crypto from "k6/crypto";
import http from "k6/http";
import { check } from "k6";
import exec from "k6/execution";

const baseUrl = (__ENV.BASE_URL || "").replace(/\/+$/, "");
const webhookSecret = __ENV.ALERT_WEBHOOK_SECRET || "";
const tenantId = __ENV.TENANT_ID || "step4-acceptance";

if (!baseUrl || !webhookSecret) {
  throw new Error("BASE_URL and ALERT_WEBHOOK_SECRET are required");
}

export const options = {
  scenarios: {
    operational_reads: {
      executor: "constant-vus",
      exec: "operationalReads",
      vus: Number(__ENV.READ_VUS || 20),
      duration: __ENV.DURATION || "2m",
    },
    alert_ingestion: {
      executor: "constant-arrival-rate",
      exec: "alertIngestion",
      rate: Number(__ENV.ALERT_RATE || 20),
      timeUnit: "1s",
      duration: __ENV.DURATION || "2m",
      preAllocatedVUs: Number(__ENV.ALERT_VUS || 30),
      maxVUs: Number(__ENV.ALERT_MAX_VUS || 100),
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<500", "p(99)<1000"],
    dropped_iterations: ["count==0"],
    checks: ["rate>0.99"],
  },
};

export function operationalReads() {
  const health = http.get(`${baseUrl}/healthz`, {
    tags: { operation: "healthz" },
  });
  check(health, { "healthz is 200": (response) => response.status === 200 });

  const ready = http.get(`${baseUrl}/readyz`, {
    tags: { operation: "readyz" },
  });
  check(ready, { "readyz is 200": (response) => response.status === 200 });
}

export function alertIngestion() {
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const identity = `${exec.vu.idInTest}-${exec.scenario.iterationInTest}`;
  const body = JSON.stringify({
    tenant_id: tenantId,
    source: "step4-load-acceptance",
    service_name: "synthetic-checkout",
    severity: "WARNING",
    summary: "Synthetic Step 4 capacity verification",
    starts_at: new Date().toISOString(),
    fingerprint: `step4-${identity}`,
    external_event_id: `step4-${identity}`,
  });
  const signature = crypto.hmac(
    "sha256",
    webhookSecret,
    `${timestamp}.${body}`,
    "hex",
  );
  const response = http.post(`${baseUrl}/api/v1/alerts`, body, {
    headers: {
      "Content-Type": "application/json",
      "X-DevOps-Agent-Timestamp": timestamp,
      "X-DevOps-Agent-Signature": `sha256=${signature}`,
    },
    tags: { operation: "alert_ingestion" },
  });
  check(response, {
    "alert accepted": (result) => result.status === 200,
    "trace returned": (result) => Boolean(result.headers["X-Trace-Id"]),
  });
}
