import unittest
from action_policy import parse

class ActionPolicyTests(unittest.TestCase):
    def test_safe_file(self):
        actions=parse("FILE: notes/a.txt\nhello")
        self.assertEqual(actions[0].payload["path"],"notes/a.txt")
    def test_reject_traversal(self):
        with self.assertRaises(ValueError): parse("FILE: ../secret.txt\nno")
    def test_reject_external_github(self):
        with self.assertRaises(ValueError): parse("GITHUB: https://evil.example/x/y\n")

if __name__=="__main__": unittest.main()
