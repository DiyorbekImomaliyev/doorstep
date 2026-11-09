"""Outbox job kinds that code outside the worker may queue.

A module with no imports, so the case agent can queue the tracking job without reaching any PayPal
write module (the import contracts forbid that, even indirectly).
"""

ADD_TRACKING = "paypal.add_tracking"  # payload: {"shipment_id": <Doorstep shipment id>}
