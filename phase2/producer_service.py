"""
Producer Agent as an autonomous A2A service.

On startup, with nobody telling it what to do, the producer:

  1. generates its own key and onboards with the Registry agent (A2A)
  2. reads the Consumer's Agent Card to learn the key that signs receipts
  3. works through its ERP's open invoices (MCP tools), sending each as a signed
     envelope to the Consumer (A2A)

and it handles what comes back on its own:

  COMPLETED       verify the signed receipt and that the receipt's hash matches what
                  was sent, then mark the invoice settled in the ERP
  INPUT_REQUIRED  credential expired: renew with the Registry, resend (if the
                  Registry refuses to renew, the business is out: halt)
  REJECTED scope  not authorized for that record type: mark it, carry on
  REJECTED status the business was revoked: stop sending, hold every remaining invoice
  unreachable     back off and retry, then give up on that invoice

Skills: get_report (what has it done so far)

Run:  python -m phase2.producer_service --port 9103 --registry-url http://127.0.0.1:9101 \
          --consumer-url http://127.0.0.1:9102
"""

import argparse
import asyncio

from trust import crypto
from trust.producer import ProducerAgent

from . import mcp_util
from .a2a_util import (COMPLETED, INPUT_REQUIRED, REJECTED, Outcome, build_app, call, fetch_card,
                       log_event, make_card, serve, wait_ready)
from .consumer_service import RECEIPT_KEY_EXTENSION


class ProducerService:
    def __init__(self, business_id: str, registry_url: str, consumer_url: str, erp,
                 interval: float, ttl: int, log_path: str | None, wire_path: str | None):
        self.business_id = business_id
        self.registry_url, self.consumer_url, self.erp = registry_url, consumer_url, erp
        self.interval, self.ttl = interval, ttl
        self.wire_path = wire_path
        self.log = lambda kind, **d: log_event(log_path, "producer", kind, **d)
        self.key = crypto.new_keypair()
        self.agent: ProducerAgent | None = None
        self.consumer_receipt_key = ""
        self.halted = False
        self.done = False
        self.results: dict[str, str] = {}
        self.renewals = 0

    # --- setup -------------------------------------------------------------

    async def onboard(self) -> None:
        out = await call(self.registry_url, "register_business", {
            "business_id": self.business_id,
            "agent_public_key": crypto.public_key_b64(self.key),
            "scopes": ["trade_payment", "credit_inquiry"],  # asks for more than it may get
            "ttl_seconds": self.ttl,
        })
        if out.state != COMPLETED:
            raise RuntimeError(f"onboarding refused: {out.text}")
        self.agent = ProducerAgent(self.key, out.data["credential"])
        self.log("onboarded", business_id=self.business_id,
                 granted=out.data["credential"]["claims"]["scopes"])

    async def learn_consumer(self) -> None:
        card = await fetch_card(self.consumer_url)
        for ext in card["capabilities"].get("extensions", []):
            if ext["uri"] == RECEIPT_KEY_EXTENSION:
                self.consumer_receipt_key = ext["params"]["public_key"]
        if not self.consumer_receipt_key:
            raise RuntimeError("consumer card does not publish a receipt key")
        self.log("learned_consumer", agent=card["name"])

    async def renew(self) -> bool:
        """Ask the registry for a fresh credential. False means it refused (e.g. revoked)."""
        out = await call(self.registry_url, "renew_credential", {
            "business_id": self.business_id,
            "agent_public_key": crypto.public_key_b64(self.key),
            "ttl_seconds": self.ttl,
        })
        if out.state != COMPLETED:
            self.log("renewal_refused", reason=out.text)
            return False
        self.agent = ProducerAgent(self.key, out.data["credential"])
        self.renewals += 1
        self.log("renewed", business_id=self.business_id)
        return True

    # --- the autonomous loop ----------------------------------------------

    async def run_forever(self) -> None:
        try:
            await self.onboard()
            await self.learn_consumer()
            invoices = (await self.erp.call("list_open_invoices"))["invoices"]
            for invoice in invoices:
                txn = {k: v for k, v in invoice.items() if k != "status"}
                if self.halted:
                    await self._record(txn["txn_id"], "held")
                    continue
                await self._record(txn["txn_id"], await self.deliver(txn))
                await asyncio.sleep(self.interval)
        except Exception as exc:
            self.log("crashed", reason=str(exc))
        finally:
            self.done = True
            self.log("finished", results=dict(self.results))

    async def _record(self, txn_id: str, status: str) -> None:
        self.results[txn_id] = status
        await self.erp.call("mark_invoice", txn_id=txn_id, status=status)

    async def deliver(self, txn: dict) -> str:
        for attempt in range(4):
            envelope = self.agent.send(txn)
            if self.wire_path:  # a network tap, for the adversary scenarios
                log_event(self.wire_path, "wire", "envelope", txn_id=txn["txn_id"], envelope=envelope)
            self.log("send", txn_id=txn["txn_id"], type=txn["type"], amount=txn["amount"])
            try:
                out = await call(self.consumer_url, "submit_transaction", {"envelope": envelope})
            except Exception as exc:
                self.log("retry", txn_id=txn["txn_id"], reason=f"consumer unreachable: {exc}")
                await asyncio.sleep(0.5 * (attempt + 1))
                continue

            if out.state == COMPLETED:
                receipt = out.data["receipt"]
                good = (ProducerAgent.verify_receipt(receipt, self.consumer_receipt_key)
                        and receipt["claims"]["payload_hash"] == crypto.sha256(txn)
                        and receipt["claims"]["txn_id"] == txn["txn_id"])
                self.log("settled" if good else "bad_receipt", txn_id=txn["txn_id"])
                return "settled" if good else "bad_receipt"

            if out.state == INPUT_REQUIRED:
                self.log("credential_expired", txn_id=txn["txn_id"])
                if not await self.renew():
                    # The registry will not re-issue: the business is no longer active.
                    self.halted = True
                    self.log("halt", reason="registry refused renewal: holding all remaining invoices")
                    return "held"
                continue

            if out.state == REJECTED:
                check = out.data.get("check", "?")
                self.log("refused", txn_id=txn["txn_id"], check=check, reason=out.text)
                if check == "status":
                    self.halted = True
                    self.log("halt", reason="business revoked: holding all remaining invoices")
                    return "held"
                return f"rejected:{check}"

            self.log("retry", txn_id=txn["txn_id"], reason=out.text)
            await asyncio.sleep(0.5 * (attempt + 1))
        return "gave_up"

    async def get_report(self, args: dict) -> Outcome:
        return Outcome(COMPLETED, {
            "done": self.done, "halted": self.halted, "renewals": self.renewals,
            "results": self.results,
        })


async def run(port: int, registry_url: str, consumer_url: str, business_id: str,
              interval: float, ttl: int, log_path: str | None, wire_path: str | None) -> None:
    await wait_ready(registry_url)
    await wait_ready(consumer_url)
    async with mcp_util.launch("phase2.mcp_erp") as erp:
        service = ProducerService(business_id, registry_url, consumer_url, erp,
                                  interval, ttl, log_path, wire_path)
        card = make_card("Producer Agent", "Data provider. Sends signed trade-payment records.", port,
                         [("get_report", "What this agent has sent and how each was decided")])
        app = build_app(card, {"get_report": service.get_report})
        work = asyncio.create_task(service.run_forever())
        try:
            await serve(app, port)
        finally:
            work.cancel()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9103)
    ap.add_argument("--registry-url", required=True)
    ap.add_argument("--consumer-url", required=True)
    ap.add_argument("--business-id", default="BIZ-100245")
    ap.add_argument("--interval", type=float, default=0.7)
    ap.add_argument("--ttl", type=int, default=3, help="credential lifetime in seconds (short, to force renewal)")
    ap.add_argument("--log")
    ap.add_argument("--wire")
    a = ap.parse_args()
    asyncio.run(run(a.port, a.registry_url, a.consumer_url, a.business_id,
                    a.interval, a.ttl, a.log, a.wire))


if __name__ == "__main__":
    main()
