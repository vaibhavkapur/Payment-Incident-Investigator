import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { pool } from "../src/db.js";
import { processStoredEvent } from "../src/processing.js";
import { setFault } from "../src/faults.js";
import { constructVerifiedEvent, signTestPayload } from "../src/provider/stripe.js";

const ORG = "11111111-1111-1111-1111-111111111111";
const CONN = "33333333-0000-0000-0000-000000000001";

async function makeOrderWithEvent() {
  const order = (await pool.query(`insert into orders (org_id, external_ref, customer_ref, amount_minor, currency, status) values ($1, $2, 'cust_t', 1999, 'usd', 'pending') returning id`, [ORG, `T-${randomUUID().slice(0, 8)}`])).rows[0];
  const ref = `pi_test_${randomUUID().slice(0, 8)}`;
  await pool.query(`insert into payment_attempts (org_id, order_id, provider_connection_id, provider_payment_ref, idempotency_key, status, amount_minor, currency) values ($1,$2,$3,$4,$5,'succeeded',1999,'usd')`, [ORG, order.id, CONN, ref, `k-${ref}`]);
  const payload = { id: `evt_test_${randomUUID().slice(0, 8)}`, type: "payment_intent.succeeded", data: { object: { id: ref, object: "payment_intent", amount: 1999, currency: "usd", status: "succeeded", metadata: { order_id: order.id, org_id: ORG } } } };
  const ev = (await pool.query(`insert into provider_events (org_id, provider_connection_id, provider_event_id, event_type, provider_payment_ref, environment, signature_verified, payload, event_time) values ($1,$2,$3,'payment_intent.succeeded',$4,'simulated',true,$5,now()) returning id`, [ORG, CONN, payload.id, ref, JSON.stringify(payload)])).rows[0];
  return { orderId: order.id as string, eventRowId: ev.id as string };
}

before(async () => { await pool.query("select 1"); });
after(async () => { await pool.end(); });

test("repeated processing of the same event creates exactly one business effect", async () => {
  const { orderId, eventRowId } = await makeOrderWithEvent();
  const first = await processStoredEvent({ orgId: ORG, eventRowId, triggeredBy: "webhook" });
  const second = await processStoredEvent({ orgId: ORG, eventRowId, triggeredBy: "webhook" });
  assert.equal(first.outcome, "applied");
  assert.equal(second.outcome, "duplicate");
  const order = (await pool.query(`select status, version from orders where id = $1`, [orderId])).rows[0];
  assert.equal(order.status, "paid");
  assert.equal(order.version, 2);
});

test("a failed handler run can be reprocessed; the same recovery job never applies twice", async () => {
  const { orderId, eventRowId } = await makeOrderWithEvent();
  await setFault(ORG, orderId, "handler_crash");
  const failed = await processStoredEvent({ orgId: ORG, eventRowId, triggeredBy: "webhook" });
  assert.equal(failed.outcome, "failed");
  assert.equal((await pool.query(`select status from orders where id = $1`, [orderId])).rows[0].status, "pending");

  const job = randomUUID();
  const repaired = await processStoredEvent({ orgId: ORG, eventRowId, triggeredBy: "reprocess", recoveryJobId: job });
  assert.equal(repaired.outcome, "applied");
  const replay = await processStoredEvent({ orgId: ORG, eventRowId, triggeredBy: "reprocess", recoveryJobId: job });
  assert.equal(replay.already_recorded, true);
  assert.equal(replay.outcome, "applied");
  assert.equal((await pool.query(`select count(*)::int as n from processing_attempts where provider_event_row_id = $1 and outcome = 'applied'`, [eventRowId])).rows[0].n, 1);
  assert.equal((await pool.query(`select status from orders where id = $1`, [orderId])).rows[0].status, "paid");
});

test("events from another organization are not visible to the handler", async () => {
  const { eventRowId } = await makeOrderWithEvent();
  await assert.rejects(processStoredEvent({ orgId: "22222222-2222-2222-2222-222222222222", eventRowId, triggeredBy: "webhook" }), /not found in organization/);
});

test("webhook signature verification rejects tampered payloads", () => {
  const secret = "whsec_test";
  const payload = JSON.stringify({ id: "evt_1", type: "payment_intent.succeeded", data: { object: {} } });
  const header = signTestPayload(payload, secret);
  assert.equal(constructVerifiedEvent(payload, header, secret).id, "evt_1");
  assert.throws(() => constructVerifiedEvent(payload.replace("evt_1", "evt_2"), header, secret));
  assert.throws(() => constructVerifiedEvent(payload, header, "whsec_other"));
});
