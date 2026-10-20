"""
Producer Agent: acts for a data provider (for example, a supplier reporting
trade payment experiences). It packages each transaction into a signed
envelope that carries its Business Identity Credential.
"""

import time
import uuid

from . import crypto


class ProducerAgent:
    def __init__(self, private_key, credential: dict):
        self._private_key = private_key
        self.credential = credential
        self.business_id = credential["claims"]["business_id"]

    def send(self, transaction: dict) -> dict:
        body = {
            "business_id": self.business_id,
            "payload": transaction,
            "nonce": uuid.uuid4().hex,
            "sent_at": int(time.time()),
        }
        return {
            "credential": self.credential,
            "body": body,
            "signature": crypto.sign(self._private_key, body),
        }

    @staticmethod
    def verify_receipt(receipt: dict, consumer_public_key: str) -> bool:
        """Producer confirms the consumer really accepted it: proof of settlement."""
        return crypto.verify(consumer_public_key, receipt["claims"], receipt["signature"])
