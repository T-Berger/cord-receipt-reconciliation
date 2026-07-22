import assert from "node:assert/strict";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the reconciliation command center", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>Receipt Review \| Reconciliation Command Center<\/title>/i);
  assert.match(html, /Partially approved/);
  assert.match(html, /IDR[\s\u00a0]*40,000/);
  assert.match(html, /Mistral OCR vs\. Docling/);
  assert.match(html, /OCR Inspector/);
  assert.match(html, /Reveal sanitized OCR transcript/);
  assert.match(html, /Agrees after normalization/);
  assert.match(html, /native confidence/i);
  assert.match(html, /Finance review/);
  assert.match(html, /Receipt case queue/);
  assert.match(html, /Architecture pipeline/);
  assert.match(html, /Langfuse trace/);
  assert.match(html, /CLAIM TOTAL MISMATCH/i);
  assert.doesNotMatch(html, /Your site is taking shape|react-loading-skeleton/);
});

test("renders evidence-backed metrics from the sanitized demo payload", async () => {
  const response = await render();
  const html = await response.text();

  assert.match(html, /DEMO-CLAIM-17/);
  assert.match(html, /Synthetic employee/);
  assert.match(html, /100%/);
  assert.match(html, /22 \/ 22 checks passed/);
  assert.match(html, /Demo run 17/);
  assert.match(html, /9 stages \/[\s\S]{0,50}10[\s\S]{0,50}total observations/);
  assert.match(html, /Backend trace verified/);
  assert.match(html, /mirror errors/);
  assert.match(html, /Langfuse cloud verified/);
  assert.match(html, /Authenticated cloud read returned/);
  assert.doesNotMatch(html, /EMP-\d+|CLM-\d{8}|[a-f0-9]{32}|C:[\\/]+Users[\\/]+/i);
});
