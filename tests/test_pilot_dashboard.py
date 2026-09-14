import unittest

from yanxu.pilot_dashboard import render_dashboard, render_login


class PilotDashboardContractTests(unittest.TestCase):
    def test_login_does_not_write_token_to_browser_storage(self):
        page = render_login()
        self.assertNotIn("localStorage", page)
        self.assertNotIn("sessionStorage", page)
        self.assertIn("HttpOnly", page)

    def test_dashboard_escapes_dynamic_values(self):
        page = render_dashboard({
            "created_at": "2026-09-14T00:00:00Z",
            "organization_slug": "team",
            "role": "owner",
            "stats": {"workspaces": 0, "snapshots": 0, "projects": 1,
                      "ci_passing": 0, "pending_events": 0, "cost_records": 0,
                      "acceptance_passed": 0, "support_minutes": 0},
            "onboarding": [],
            "workspaces": [],
            "projects": [{"workspace": "demo", "repo": "<script>alert(1)</script>",
                          "pr": 1, "ci": "PENDING", "assessment": "MANUAL_REVIEW"}],
            "deliveries": [],
            "audit": [],
            "costs": [],
            "cost_summary": [],
            "acceptance": [{"id": "11111111-1111-1111-1111-111111111111",
                            "criterion": "<img src=x onerror=alert(1)>", "status": "PENDING",
                            "evidence_ref": None, "customer_confirmed": False,
                            "updated_at": "2026-09-14T00:00:00Z"}],
            "acceptance_summary": {"status": "NEEDS_REVIEW", "total": 1, "pending": 1,
                                   "passed": 0, "failed": 0, "customer_confirmed": 0},
            "support": [],
            "support_summary": {"records": 0, "minutes": 0},
            "data_boundary": "safe",
        })
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn("<img src=x onerror=alert(1)>", page)
        self.assertIn("document.addEventListener('submit'", page)
        self.assertNotIn("querySelectorAll('.acceptance-update')", page)


if __name__ == "__main__":
    unittest.main()
