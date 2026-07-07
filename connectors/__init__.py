"""connectors — pull data from where it already lives (warehouse / CRM / BI) instead of manual entry.

Each connector `fetch()`es raw source rows; a FieldMapping (mapping.py) maps the customer's columns
to our signal schema; enrichment.py fills known gaps from 3rd-party sources. The result feeds the same
churn engine. Whatever isn't available simply lowers coverage — never faked.
"""
