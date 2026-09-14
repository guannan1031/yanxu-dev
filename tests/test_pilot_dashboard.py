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
            "role": "viewer",
            "stats": {"workspaces": 0, "snapshots": 0, "projects": 1,
                      "ci_passing": 0, "pending_events": 0},
            "onboarding": [],
            "workspaces": [],
            "projects": [{"workspace": "demo", "repo": "<script>alert(1)</script>",
                          "pr": 1, "ci": "PENDING", "assessment": "MANUAL_REVIEW"}],
            "deliveries": [],
            "audit": [],
            "data_boundary": "safe",
        })
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)


if __name__ == "__main__":
    unittest.main()
