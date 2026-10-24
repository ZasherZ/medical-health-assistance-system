"""Black box (TC01-TC05) and white box tests for emergency reporting. Run: python tests.py"""
import os, tempfile, unittest
fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd); os.environ["HEALTH_DB"] = path
import app as A

class EmergencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        A.app.config["TESTING"] = True
        cls.s, cls.d = A.app.test_client(), A.app.test_client()
        cls.s.post("/register", data=dict(name="Test Student", email="t@x.com", mobile="9876543210",
                                          password="secret1", role="student", department="CSE"))
        cls.s.post("/login", data=dict(ident="t@x.com", password="secret1"))
        cls.d.post("/login", data=dict(ident="asha@college.edu", password="Doctor@123"))

    def send(self, **kw):
        data = dict(symptoms="Chest pain", details="Since morning", location="Hostel A",
                    contact="9876543210", emergency="on"); data.update(kw)
        return self.s.post("/request", data=data, follow_redirects=True).get_data(as_text=True)

    def test_tc01_valid_details(self): self.assertIn("Alert submitted", self.send())
    def test_tc02_empty_fields(self): self.assertIn("required fields", self.send(symptoms="", location=""))
    def test_tc03_valid_location(self): self.assertIn("Alert submitted", self.send(location="Library, Block B"))
    def test_tc04_alert_for_staff(self):
        self.send(symptoms="Seizure")
        self.assertIn("EMERGENCY", self.d.get("/staff").get_data(as_text=True))
    def test_tc05_invalid_contact(self): self.assertIn("Invalid contact", self.send(contact="12ab"))
    # white box: validation logic
    def test_wb_validation_logic(self): self.assertIsNone(A.validate_request("fever", "Hostel", "9876543210"))
    def test_wb_missing(self): self.assertIsNotNone(A.validate_request("", "Hostel", "9876543210"))
    def test_wb_invalid(self):
        self.assertIn("contact", A.validate_request("fever", "Hostel", "123"))
        self.assertIn("location", A.validate_request("fever", "ab", "9876543210"))
    def test_wb_data_saved(self):
        self.send(symptoms="Saved check")
        with A.app.app_context():
            self.assertTrue(A.q("SELECT 1 FROM requests WHERE symptoms='Saved check'", one=True))

if __name__ == "__main__":
    unittest.main()
