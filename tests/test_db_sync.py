"""DB Sync helpers and optional live CFG connection tests.

Live tests run only when ACP_ORACLE_PASSWORD is set. Never commit passwords.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from acp_importer.db_sync import (
    OracleEndpoint,
    _is_dbhome_client,
    _net_config_dir,
    _quote_ident,
    _should_retry_connect_mode,
    choose_order_column,
    classify_retry,
    test_connection,
)


class DbSyncHelperTests(unittest.TestCase):
    def test_quote_ident_uppercases(self):
        self.assertEqual(_quote_ident("ifsapp"), "IFSAPP")

    def test_quote_ident_rejects_injection(self):
        with self.assertRaises(ValueError):
            _quote_ident("IFSAPP; DROP TABLE X")

    def test_sid_and_service_dsn(self):
        sid = OracleEndpoint("10.0.0.1", 1521, "thorcfg1_1", "u", "p", connect_as="sid")
        svc = OracleEndpoint("10.0.0.1", 1521, "thorcfg1_1", "u", "p", connect_as="service")
        self.assertIn("(SID=thorcfg1_1)", sid.dsn())
        self.assertIn("(SERVICE_NAME=thorcfg1_1)", svc.dsn())

    def test_retry_on_checksum_error(self):
        self.assertTrue(_should_retry_connect_mode(Exception("ORA-12569: TNS:packet checksum failure")))
        self.assertFalse(_should_retry_connect_mode(Exception("ORA-01017: invalid username/password")))

    def test_net_config_dir_has_sqlnet(self):
        config_dir = Path(_net_config_dir())
        self.assertTrue((config_dir / "sqlnet.ora").is_file(), config_dir)

    def test_dbhome_detection(self):
        self.assertTrue(_is_dbhome_client(r"C:\app\Administrator\product\19.0.0\dbhome_1\bin"))
        self.assertFalse(_is_dbhome_client(r"C:\oracle\instantclient_19_21"))

    def test_retry_skips_complete_tables(self):
        self.assertEqual(classify_retry(10, 10), "complete")
        self.assertEqual(classify_retry(10, 12), "target_ahead")
        self.assertEqual(classify_retry(10, 4), "incomplete")
        self.assertEqual(classify_retry(10, None), "missing_target")
        self.assertEqual(classify_retry(None, 0), "missing_source")
        self.assertEqual(classify_retry(1001, 1000), "complete")
        self.assertEqual(classify_retry(1001, 1001), "target_ahead")
        self.assertEqual(classify_retry(1001, 20), "incomplete")

    def test_order_column_prefers_table_specific_dates(self):
        columns = [
            {"name": "ROWKEY", "dataType": "VARCHAR2"},
            {"name": "DESCRIPTION", "dataType": "VARCHAR2"},
            {"name": "DT_CRE", "dataType": "DATE"},
            {"name": "QTY", "dataType": "NUMBER"},
        ]
        chosen = choose_order_column(columns, pk_columns=["ROWKEY"])
        self.assertEqual(chosen["columns"], ["DT_CRE"])
        self.assertTrue(chosen["reliable"])

    def test_order_column_uses_numeric_primary_key(self):
        columns = [
            {"name": "ACTIVITY_SEQ", "dataType": "NUMBER"},
            {"name": "NOTE", "dataType": "VARCHAR2"},
        ]
        chosen = choose_order_column(columns, pk_columns=["ACTIVITY_SEQ"])
        self.assertEqual(chosen["columns"], ["ACTIVITY_SEQ"])

    def test_order_column_logs_unreliable_fallback(self):
        columns = [
            {"name": "ROWKEY", "dataType": "VARCHAR2"},
            {"name": "DESCRIPTION", "dataType": "VARCHAR2"},
        ]
        chosen = choose_order_column(columns, pk_columns=["ROWKEY"])
        self.assertEqual(chosen["columns"], [])
        self.assertFalse(chosen["reliable"])


@unittest.skipUnless(os.environ.get("ACP_ORACLE_PASSWORD"), "Set ACP_ORACLE_PASSWORD to run live CFG tests")
class LiveCfgConnectionTests(unittest.TestCase):
    def _endpoint(self, *, connect_as: str, thick: bool) -> OracleEndpoint:
        return OracleEndpoint(
            host=os.environ.get("ACP_ORACLE_HOST", "10.242.66.100"),
            port=int(os.environ.get("ACP_ORACLE_PORT", "1521")),
            service=os.environ.get("ACP_ORACLE_SERVICE", "thorcfg1_1"),
            user=os.environ.get("ACP_ORACLE_USER", "IFSINFO"),
            password=os.environ["ACP_ORACLE_PASSWORD"],
            connect_as=connect_as,
            thick=thick,
        )

    def test_thin_service_or_sid(self):
        errors = []
        for mode in ("service", "sid"):
            try:
                result = test_connection(self._endpoint(connect_as=mode, thick=False))
                self.assertTrue(result.get("ok"))
                return
            except Exception as exc:
                errors.append(f"{mode}/thin: {exc}")
        self.fail("thin connect failed:\n" + "\n".join(errors))

    def test_thick_service_or_sid(self):
        errors = []
        for mode in ("service", "sid"):
            try:
                result = test_connection(self._endpoint(connect_as=mode, thick=True))
                self.assertTrue(result.get("ok"))
                return
            except Exception as exc:
                errors.append(f"{mode}/thick: {exc}")
        self.fail("thick connect failed:\n" + "\n".join(errors))
