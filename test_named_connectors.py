#!/usr/bin/env python3
"""
Tests for the named connectors (Salesforce / Stripe / Tableau). Uses injected FAKE clients — proving
each implements the Connector interface and returns flat rows that FieldMapping can consume, with no
SDK or credentials. Run: python test_named_connectors.py
"""
from __future__ import annotations

import sys

from connectors.base import Connector
from connectors.salesforce import SalesforceConnector
from connectors.stripe import StripeConnector
from connectors.tableau import TableauConnector
from mapping import FieldMapping

passed = failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}")


class FakeSalesforce:
    def query_all(self, soql):
        return {"records": [
            {"attributes": {"type": "Account", "url": "/x"}, "Name": "BigCo", "ARR__c": 500000},
            {"attributes": {"type": "Account"}, "Name": "SmallCo", "ARR__c": 120000},
        ]}


class FakeStripe:
    class Subscription:
        @staticmethod
        def list(**kwargs):
            return {"data": [{"id": "sub_1", "customer": "BigCo", "status": "active"},
                             {"id": "sub_2", "customer": "SmallCo", "status": "active"}]}


class FakeTableau:
    def query_datasource(self, datasource_id, query):
        return [{"account": "BigCo", "health": 42}, {"account": "SmallCo", "health": 88}]


def main() -> None:
    # ── Salesforce: strips `attributes`, returns flat rows; satisfies Connector ─
    sf = SalesforceConnector(FakeSalesforce(), "SELECT Name, ARR__c FROM Account")
    check("SalesforceConnector is a Connector", isinstance(sf, Connector))
    sf_rows = sf.fetch()
    check("SF fetch returns 2 rows", len(sf_rows) == 2)
    check("SF strips attributes metadata", all("attributes" not in r for r in sf_rows))
    check("SF row has real fields", sf_rows[0]["Name"] == "BigCo" and sf_rows[0]["ARR__c"] == 500000)
    # FieldMapping consumes it uniformly
    mapped = FieldMapping({"account_name": "Name", "booked_arr": "ARR__c"}).apply(sf_rows)
    check("SF rows map into our schema", mapped[0]["account_name"] == "BigCo" and mapped[0]["booked_arr"] == 500000)

    # ── Stripe: reads <Resource>.list().data ────────────────────────────────
    stripe = StripeConnector(FakeStripe(), resource="Subscription", params={"limit": 100})
    check("StripeConnector is a Connector", isinstance(stripe, Connector))
    st_rows = stripe.fetch()
    check("Stripe fetch returns 2 rows", len(st_rows) == 2 and st_rows[0]["id"] == "sub_1")

    # ── Tableau: pulls a published data source ──────────────────────────────
    tab = TableauConnector(FakeTableau(), datasource_id="ds-123")
    check("TableauConnector is a Connector", isinstance(tab, Connector))
    tb_rows = tab.fetch()
    check("Tableau fetch returns rows", len(tb_rows) == 2 and tb_rows[0]["account"] == "BigCo")

    print(f"\n{passed}/{passed + failed} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
