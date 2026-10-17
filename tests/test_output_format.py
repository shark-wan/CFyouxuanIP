from contextlib import nullcontext
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import apply_sg_ranking
import collect_cf_ips
import fallback_ip
import heartbeat_guard
import sg_optimizer
import validate_outputs


class OutputFormatTests(unittest.TestCase):
    def test_validator_accepts_current_aggregate_and_ranked_output(self):
        self.assertEqual(validate_outputs.validate_aggregate(ROOT / "ip.aggregate.txt"), [])
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "ip.txt"
            output.write_text(
                "192.0.2.1:443#AAA-SG1\n"
                "192.0.2.2:443#BBB-SG2\n"
                "192.0.2.3:443#CCC-SG3\n"
                "192.0.2.4:443#DDD-SG4\n"
                "192.0.2.5:443#EEE-SG5\n"
                "192.0.2.6:443#1.JP6\n",
                encoding="utf-8",
            )
            self.assertEqual(validate_outputs.validate_ip(output), [])

    def test_collector_handles_unpadded_urlsafe_base64_and_ipv6(self):
        import base64

        source = "vless://uuid@[2001:db8::1]:443?security=tls#Singapore-1"
        encoded = base64.urlsafe_b64encode(source.encode()).decode().rstrip("=")
        self.assertEqual(collect_cf_ips.maybe_b64decode(encoded), source)
        self.assertEqual(list(collect_cf_ips.parse_items(source)), [("SG", "2001:db8::1", "443")])

        self.assertEqual(
            sg_optimizer.body_download_rate(131072, 1.5, 1.6),
            (1310720.0, 0.1),
        )
        self.assertIsNone(sg_optimizer.body_download_rate(0, 1.5, 1.6))
        self.assertIsNone(sg_optimizer.body_download_rate(131072, 1.6, 1.6))

    def test_tcp_probes_never_report_zero_milliseconds(self):
        row = ("192.0.2.1", 443, "SG1")
        node = {"address": row[0], "port": row[1]}
        with patch.object(fallback_ip.socket, "create_connection", return_value=nullcontext()), patch.object(
            fallback_ip.time, "perf_counter", side_effect=[1.0, 1.0, 2.0, 2.0]
        ):
            self.assertEqual(fallback_ip.probe(row)[3], 0.01)

        with patch.object(sg_optimizer.socket, "create_connection", return_value=nullcontext()), patch.object(
            sg_optimizer.time, "perf_counter", side_effect=[1.0, 1.0, 2.0, 2.0, 3.0, 3.0]
        ):
            self.assertEqual(sg_optimizer.tcp_probe(node)["tcp_ms"], 0.01)

    def test_fallback_keeps_speed_ranks_then_numbers_tcp_remainder(self):
        ranked = [
            (f"192.0.2.{index}", 443, f"SG{index}", 20.0 + index, 1000 - index)
            for index in range(1, 6)
        ]
        reachable = [
            ("192.0.2.6", 443, "JP6", 10.0),
            ("192.0.2.1", 443, "SG1", 21.0),
            ("192.0.2.7", 443, "SG7", 30.0),
            ("192.0.2.2", 443, "SG2", 22.0),
        ]

        output = fallback_ip.format_output(ranked, reachable)

        self.assertEqual(output[:2], [
            "192.0.2.1:443#AAA-SG1",
            "192.0.2.2:443#BBB-SG2",
        ])
        self.assertEqual(output[5:], [
            "192.0.2.6:443#1.JP6",
            "192.0.2.7:443#2.SG7",
        ])

    def test_fallback_degraded_prefix_is_accepted_by_validator(self):
        ranked = [
            (f"192.0.2.{index}", 443, f"SG{index}", 20.0 + index, 1000 - index)
            for index in range(1, 6)
        ]
        reachable = [("192.0.2.6", 443, "JP6", 10.0)]

        output = fallback_ip.format_output(ranked, reachable, prefix="旧-")

        self.assertEqual(output[0], "192.0.2.1:443#旧-AAA-SG1")
        self.assertEqual(output[4], "192.0.2.5:443#旧-EEE-SG5")
        self.assertEqual(output[5], "192.0.2.6:443#1.JP6")

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ip.txt"
            path.write_text("\n".join(output) + "\n", encoding="utf-8")
            self.assertEqual(validate_outputs.validate_ip(path), [])

    def test_validator_accepts_unranked_abandon_output(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ip.txt"
            path.write_text(
                "192.0.2.1:443#SG1\n"
                "192.0.2.2:443#JP2\n",
                encoding="utf-8",
            )
            self.assertEqual(validate_outputs.validate_ip(path), [])

    def test_heartbeat_guard_reports_degraded_and_abandon_thresholds(self):
        import heartbeat_guard
        from datetime import datetime, timedelta, timezone

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "device-status.json"
            env = {"CF_HEARTBEAT_MAX_AGE": "5400"}

            def write(age_seconds):
                stamp = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
                path.write_text(
                    json.dumps({
                        "updated_at": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "publish": {"ip_ok": True},
                    }),
                    encoding="utf-8",
                )

            with patch.object(heartbeat_guard, "Path", return_value=path), patch.dict(
                heartbeat_guard.os.environ, env, clear=False
            ), patch("sys.argv", ["heartbeat_guard.py"]):
                import contextlib, io

                for age, expected in (
                    (300, ("false", "false", "false")),
                    (7200, ("true", "true", "false")),
                    (30000, ("true", "true", "true")),
                ):
                    write(age)
                    buffer = io.StringIO()
                    with contextlib.redirect_stdout(buffer):
                        heartbeat_guard.main()
                    values = dict(
                        line.split("=") for line in buffer.getvalue().strip().splitlines()
                    )
                    self.assertEqual(
                        (values["stale"], values["degraded"], values["abandon"]),
                        expected,
                        f"age={age}",
                    )

    def test_ranking_script_numbers_remainder_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ranking_file = root / "sg-ranking.json"
            ip_file = root / "ip.txt"
            ranking_file.write_text(json.dumps({
                "slots": [{
                    "address": "192.0.2.9",
                    "port": 443,
                    "source_name": "SG9",
                }],
                "jp_slots": [],
            }), encoding="utf-8")
            ip_file.write_text(
                "192.0.2.1:443#JP1\n"
                "192.0.2.2:443#SG2\n"
                "192.0.2.3:443#HK3\n",
                encoding="utf-8",
            )

            with patch.object(apply_sg_ranking, "RANKING", ranking_file), patch.object(
                apply_sg_ranking, "IP_FILE", ip_file
            ):
                apply_sg_ranking.main()
                first = ip_file.read_text(encoding="utf-8")
                apply_sg_ranking.main()
                second = ip_file.read_text(encoding="utf-8")

            self.assertEqual(first, second)
            self.assertEqual(first.splitlines(), [
                "192.0.2.9:443#AAA-SG9",
                "192.0.2.1:443#1.JP1",
                "192.0.2.2:443#2.SG2",
                "192.0.2.3:443#3.HK3",
            ])


if __name__ == "__main__":
    unittest.main()
